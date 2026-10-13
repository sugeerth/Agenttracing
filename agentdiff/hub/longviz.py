"""Long runs, drawn: hours and days at the scale they ran.

- :func:`long_overview` — the whole run on one line. Idle between sessions
  is compressed to a break that says how long it was; inside a session
  the clock is true. Rows: the sessions, the bursts coloured by how they
  went, every call as a density of activity, the checks, then the loop
  and the stall as brackets. Every burst opens its calls.
- :func:`rhythm_grid` — a row per day, a column per hour (for a run of
  hours, a row per hour and a column per 5 minutes): when it worked,
  when it failed, when it moved forward.
- :func:`chapters_table` — the run's story as rows. A loop of bursts is
  one row, a stretch of filler (bursts with no check changing) is one
  row, and idle is a divider.
- :func:`burst_calls` — every call of one burst, or of one time range:
  on its clock in a lane per activity, then in order at an even pace,
  with lines that show where the clock ran fast or slow.
- :func:`pace_chart` — calls, seconds per call and tokens per step over
  the run, as three small charts, each on its own axis.
- :func:`phase_treemap` — where the working time went: a box per phase
  (a burst, or a loop of bursts), a tile per tool, area by seconds.
- :func:`diff_timeline` — two long runs aligned at the checkpoints they
  share, their tool use mirrored above and below the line.

Server-side SVG, no script needed. The hub's ``longview.js`` adds the lens
on top: a fisheye with a halo round the cursor, and phase-by-phase
navigation (``agentdiff/hub/static/longview.js``).
"""

from __future__ import annotations

import bisect
import difflib
import math
from collections import Counter
from typing import Dict, List, Optional

from ..longrun import check_key, dur, when
from .viz import ACTIVITIES, _DARK_GLYPH, e, legend

__all__ = ["long_overview", "rhythm_grid", "chapters_table", "burst_calls", "pace_chart", "phase_treemap",
           "diff_timeline", "phases", "STATUS", "Axis", "lens_payload"]

#: burst status -> (fill, glyph, what it means)
STATUS = {
    "progress": ("var(--sg)", "✓", "progress: a check started passing"),
    "regressed": ("var(--sc)", "✗", "regressed: a check that passed now fails"),
    "stuck": ("var(--sc)", "↻", "stuck: repeats an earlier burst, or the same failing call"),
    "failing": ("var(--sc)", "!", "checks failing, nothing repeated"),
    "done": ("var(--sg)", "■", "answered"),
    "working": ("var(--an)", "", "working: edits and runs"),
    "exploring": ("var(--at)", "", "reading"),
}
_MINOR = ' style="stroke-opacity:.5"'
_BAD_EDGE = ' style="stroke:var(--sc);stroke-width:2"'
_ORDER = ("explore", "research", "plan", "think", "edit", "run", "verify", "delegate", "other")
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


def long_overview(r: dict, items: List[dict], *, width: int = 980, base: str = "", t0: Optional[float] = None,
                  t1: Optional[float] = None, live: bool = False) -> str:
    """The whole run (or ``t0``..``t1`` of it) on one line: sessions, bursts,
    every call as a density, checks, the loop and the stall."""
    if not r.get("bursts"):
        return '<p class="muted">No steps to draw.</p>'
    left, right = 70, 12
    x0, x1 = left, width - right
    zoom = t0 is not None
    axis = Axis(r["sessions"], x0, x1, t0=t0, t1=t1)
    lo, hi = (t0, t1) if zoom else (0.0, r["span_s"])
    sa = r.get("started_at")
    span = r["span_s"]
    y_s, y_b, y_d, d_h, y_c = 22, 44, 70, 62, 152
    y_br = 172
    height = 220
    out = [f'<svg class="viz soft long" viewBox="0 0 {width} {height}" width="{width}" role="img" '
           f'aria-label="{e(r.get("sentence"))}">']
    # the clock
    for x, lab, major in _ticks(axis, sa, span):
        out.append(f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{y_s - 4}" y2="{y_c + 10}"'
                   f'{_MINOR if not major else ""}/>'
                   f'<text x="{x + 2:.1f}" y="12" style="font-size:10px{";font-weight:700" if major else ""}">{e(lab)}</text>')
    for lab, y in (("sessions", y_s + 12), ("bursts", y_b + 13), ("calls", y_d + d_h / 2 + 4), ("checks", y_c + 4)):
        out.append(f'<text x="4" y="{y}" style="font-size:10px">{lab}</text>')
    # sessions, and the idle between them
    for s, (a, b, xa, xb) in zip(r["sessions"] if not zoom else [], axis.parts):
        lab = _fit(f"S{s['n']} · {when(s['from'], sa, span)} · {s['calls']:,} calls", xb - xa - 6)
        out.append(f'<a href="{e(base)}&amp;session={s["n"]}#p-long"><rect class="hot" x="{xa:.1f}" y="{y_s}" '
                   f'width="{max(1.0, xb - xa):.1f}" height="16" rx="3" style="fill:var(--grid);opacity:.85">'
                   f'<title>session {s["n"]}: {e(when(s["from"], sa, span))} to {e(when(s["to"], sa, span))}, '
                   f'{e(dur(s["seconds"]))} ({e(dur(s["active_s"]))} working), {s["calls"]:,} calls in '
                   f'{len(s["bursts"])} burst(s); {s["progress"]} step(s) forward, {s["stuck"]} burst(s) stuck — '
                   f'open it</title></rect></a>'
                   f'<text x="{xa + 4:.1f}" y="{y_s + 12}" style="font-size:10px;pointer-events:none">{e(lab)}</text>')
    for g0, g1, ga, gb in axis.gaps:
        mid = (ga + gb) / 2
        out.append(f'<g aria-label="idle {e(dur(g1 - g0))}"><path d="M{mid - 5:.1f},{y_b + 22} l6,-22 M{mid + 1:.1f},{y_b + 22} l6,-22" '
                   f'style="stroke:var(--ink2);stroke-width:1.5;fill:none"/>'
                   f'<text x="{mid:.1f}" y="{y_d + 14}" text-anchor="middle" style="font-size:9.5px">{e(dur(g1 - g0))}</text>'
                   f'<text x="{mid:.1f}" y="{y_d + 26}" text-anchor="middle" style="font-size:9.5px">idle</text>'
                   f'<title>{e(dur(g1 - g0))} with no step at all, from {e(when(g0, sa, span))} to '
                   f'{e(when(g1, sa, span))}</title></g>')
    # bursts, by how they went
    for b in r["bursts"]:
        if b["to"] < lo or b["from"] > hi:
            continue
        xa = axis(max(lo, b["from"]))
        w = max(1.5, axis(min(hi, b["to"])) - xa)
        fill, glyph, what = STATUS.get(b["status"], STATUS["working"])
        op = ".5" if b["status"] == "failing" else "1"
        out.append(f'<a href="{e(base)}&amp;burst={b["n"]}#p-long"><rect class="hot" x="{xa:.1f}" y="{y_b}" width="{w:.1f}" '
                   f'height="20" rx="2" style="fill:{fill};opacity:{op}"><title>burst {b["n"]} · '
                   f'{e(when(b["from"], sa, span))} · {e(dur(b["seconds"]))} · {b["calls"]:,} calls · {e(what)}'
                   + (f' · {e(b["note"])}' if b["note"] else "") + ' — open its calls</title></rect></a>')
        if w >= 12 and glyph:
            out.append(f'<text class="g" x="{xa + w / 2:.1f}" y="{y_b + 14}" text-anchor="middle">{glyph}</text>')
    # every call, as a density of activity per 2px column
    col = 2.0
    n_bins = int((x1 - x0) / col) + 1
    bins: List[Counter] = [Counter() for _ in range(n_bins)]
    checks = [[0, 0] for _ in range(n_bins)]
    t_of: List[list] = [[] for _ in range(n_bins)]
    for x in items:
        if x["start"] < lo or x["start"] > hi:
            continue
        k = min(n_bins - 1, max(0, int((axis(x["start"]) - x0) / col)))
        bins[k][x["activity"]] += 1
        t_of[k].append(x["start"])
        if x["check"] is True:
            checks[k][0] += 1
        elif x["check"] is False or x["error"]:
            checks[k][1] += 1
    most = max((sum(c.values()) for c in bins), default=0) or 1
    paths: Dict[str, list] = {}
    for k, c in enumerate(bins):
        if not c:
            continue
        xb, yb = x0 + k * col, y_d + d_h
        for act in _ORDER:
            n = c.get(act)
            if not n:
                continue
            h = d_h * n / most
            yb -= h
            paths.setdefault(ACTIVITIES.get(act, ACTIVITIES["other"])[0], []).append(
                f"M{xb:.1f},{yb:.2f}h{col - 0.4:.1f}v{h:.2f}h-{col - 0.4:.1f}z")
        ts = t_of[k]
        mix = ", ".join(f"{n} {ACTIVITIES.get(a, ACTIVITIES['other'])[2]}" for a, n in c.most_common(4))
        p, f = checks[k]
        out.append(f'<rect x="{xb:.1f}" y="{y_d}" width="{col:.1f}" height="{d_h}" style="fill:transparent">'
                   f'<title>{e(when(min(ts), sa, span))}–{e(when(max(ts), sa, span))}: {sum(c.values())} step(s): '
                   f'{e(mix)}' + (f'; {p} check(s) passed' if p else "") + (f'; {f} failed' if f else "")
                   + '</title></rect>')
    for cls, ds in paths.items():
        out.insert(1, f'<path class="{cls}" d="{"".join(ds)}"/>')
    out.append(f'<text x="4" y="{y_d + d_h / 2 + 16}" style="font-size:9px">max {most}/col</text>')
    # checks: passed up, failed down
    mid = y_c
    for k, (p, f) in enumerate(checks):
        xb = x0 + k * col
        if p:
            out.append(f'<rect x="{xb:.1f}" y="{mid - min(8, 2 + p):.1f}" width="{col - .4:.1f}" height="{min(8, 2 + p):.1f}" '
                       f'style="fill:var(--sg)"/>')
        if f:
            out.append(f'<rect x="{xb:.1f}" y="{mid + 1}" width="{col - .4:.1f}" height="{min(8, 2 + f):.1f}" '
                       f'style="fill:var(--sc)"/>')
    for ev in r.get("events") or []:
        if not lo <= ev["t"] <= hi:
            continue
        xe = axis(ev["t"])
        if ev["kind"] == "progress":
            out.append(f'<text class="ok" x="{xe:.1f}" y="{mid - 10}" text-anchor="middle" style="font-size:11px">◆'
                       f'<title>step {ev["index"]}: {e(ev["check"])} passed ({e(ev["how"])}), '
                       f'{e(when(ev["t"], sa, span))}</title></text>')
        else:
            out.append(f'<text class="bad" x="{xe:.1f}" y="{mid + 20}" text-anchor="middle" style="font-size:11px">✗'
                       f'<title>step {ev["index"]}: {e(ev["check"])} failed after passing, '
                       f'{e(when(ev["t"], sa, span))}</title></text>')
    # brackets: the loop, the stall
    yb = y_br
    for lp in r.get("loops") or []:
        if lp["to"] < lo or lp["from"] > hi:
            continue
        xa, xb = axis(max(lo, lp["from"])), axis(min(hi, lp["to"]))
        lab = (f"↻ loop · bursts {lp['bursts'][0]}–{lp['bursts'][1]} · the same calls {lp['count']}× · "
               f"{dur(lp['wall_s'])} · {lp['calls']:,} calls" + (f" · {lp['failing']} failing" if lp["failing"] else ""))
        out.append(f'<rect x="{xa:.1f}" y="{y_b - 3}" width="{max(2.0, xb - xa):.1f}" height="26" rx="3" '
                   f'style="fill:none;stroke:var(--a2);stroke-width:2;stroke-dasharray:5 3"/>'
                   + _bracket(xa, xb, yb, "var(--a2)", lab, width))
        yb += 22
    st = r.get("stall")
    if st and st["active_s"] >= 600 and st["to"] >= lo and st["from"] <= hi:
        xa, xb = axis(max(lo, st["from"])), axis(min(hi, st["to"]))
        lab = (f"no progress for {dur(st['wall_s'])} · {dur(st['active_s'])} of it working · {st['calls']:,} calls"
               + (" · still going" if st.get("open") and r.get("in_progress") else ""))
        out.append(_bracket(xa, xb, yb, "var(--sc)", lab, width))
    # where to look first
    here = r.get("look_here")
    if here:
        ht = next((x["start"] for x in items if x["index"] == here["index"]), None)
        if ht is not None and lo <= ht <= hi:
            hx = axis(ht)
            anchor, tx = ("start", hx + 5) if hx < width - 220 else ("end", hx - 5)
            out.append(f'<line x1="{hx:.1f}" x2="{hx:.1f}" y1="{y_s}" y2="{y_c + 8}" style="stroke:var(--sc);stroke-width:2">'
                       f'<title>{e(here["sentence"])}</title></line>'
                       f'<text x="{tx:.1f}" y="{y_d + 11}" text-anchor="{anchor}" style="fill:var(--sc);'
                       f'font:600 11px system-ui,sans-serif;paint-order:stroke;stroke:var(--panel);stroke-width:3px">'
                       f'◆ look here: burst {here["burst"]}<title>{e(here["sentence"])}</title></text>')
    if live:
        out.append(f'<line x1="{x1:.1f}" x2="{x1:.1f}" y1="{y_s}" y2="{y_c + 8}" style="stroke:var(--a1);stroke-width:2">'
                   f'<title>now</title></line>')
    out.append("</svg>")
    used = sorted({x["activity"] for x in items})
    key = ([(f'<b style="color:{STATUS[k][0]}">{STATUS[k][1] or "▬"}</b>', k) for k in
            ("progress", "stuck", "failing", "working")]
           + [('<b style="color:var(--sg)">◆</b>', "a check started passing"),
              ('<b style="color:var(--a2)">⬚</b>', "loop of bursts"), ("<b>//</b>", "idle, compressed")])
    return "".join(out) + legend(used, key)


def _bracket(xa: float, xb: float, y: float, colour: str, label: str, width: int) -> str:
    xb = max(xb, xa + 2)
    lab_x, anchor = (xa, "start") if xa < width - 380 else (xb, "end")
    room = (width - 14 - lab_x) if anchor == "start" else (lab_x - 74)
    return (f'<path d="M{xa:.1f},{y - 5} v5 H{xb:.1f} v-5" style="fill:none;stroke:{colour};stroke-width:1.5"/>'
            f'<text x="{lab_x:.1f}" y="{y + 12}" text-anchor="{anchor}" style="fill:{colour};font:600 11px system-ui,sans-serif">'
            f'{e(_fit(label, room, 6.1))}<title>{e(label)}</title></text>')


# ------------------------------------------------------------------- rhythm
def rhythm_grid(r: dict, *, width: int = 980, base: str = "") -> str:
    g = r.get("rhythm")
    if not g or not g.get("cells"):
        return ""
    left, top = 76, 18
    cols, rows = g["cols"], g["rows"]
    cw = (width - left - 8) / cols
    ch = 22 if rows <= 12 else 16
    height = top + rows * ch + 6
    most = max(c["calls"] for c in g["cells"]) or 1
    sa = r.get("started_at")
    out = [f'<svg class="viz soft" viewBox="0 0 {width} {height}" width="{width}" role="img" '
           f'aria-label="when it worked: {e(g["unit"])}, darker is more calls">']
    every = 3 if cols == 24 else 2
    for c in range(0, cols, every):
        lab = f"{c:02d}:00" if cols == 24 else f":{c * 5:02d}"
        out.append(f'<text x="{left + c * cw + 2:.1f}" y="12" style="font-size:9.5px">{lab}</text>')
    for k in range(rows):
        t_row = g["row0_s"] + k * g["row_s"]
        if g["row_s"] >= 86400:
            lab = f"day {k + 1}"
            if sa is not None:
                import datetime as _dt
                lab += f" {_dt.datetime.fromtimestamp(sa + max(0.0, t_row), tz=_dt.timezone.utc):%a}"
        else:
            lab = when(max(0.0, t_row), sa, 3600 * 2) if sa is not None else f"+{dur(max(0.0, t_row))}"
        out.append(f'<text x="4" y="{top + k * ch + ch - 6}" style="font-size:10px">{e(lab)}</text>')
        for c in range(cols):
            out.append(f'<rect x="{left + c * cw + .5:.1f}" y="{top + k * ch + .5:.1f}" width="{cw - 1:.1f}" '
                       f'height="{ch - 1}" rx="2" style="fill:none;stroke:var(--grid)"/>')
    for c in g["cells"]:
        x, y = left + c["col"] * cw, top + c["row"] * ch
        op = 0.14 + 0.86 * math.sqrt(c["calls"] / most) if c["calls"] else 0.06
        tip = (f'{when(max(0.0, c["t0"]), sa, max(r["span_s"], 13 * 3600 if g["row_s"] >= 86400 else 3600))}: '
               f'{c["calls"]} call(s), {c["steps"]} step(s)' + (f'; {c["passed"]} check(s) passed' if c["passed"] else "")
               + (f'; {c["failed"]} failed' if c["failed"] else "") + (f'; {c["progress"]} step(s) forward' if c["progress"] else "")
               + ("; a burst here repeats an earlier one" if c["stuck"] else "") + " — open this stretch")
        out.append(f'<a href="{e(base)}&amp;t0={max(0.0, c["t0"]):.0f}&amp;t1={c["t1"]:.0f}#p-long">'
                   f'<rect class="hot" x="{x + 1:.1f}" y="{y + 1}" width="{cw - 2:.1f}" height="{ch - 2}" rx="2" '
                   f'style="fill:var(--a1);opacity:{op:.2f}"><title>{e(tip)}</title></rect></a>')
        if c["failed"]:
            out.append(f'<rect x="{x + 1:.1f}" y="{y + ch - 4}" width="{cw - 2:.1f}" height="3" style="fill:var(--sc)"/>')
        if c["progress"]:
            out.append(f'<text class="ok" x="{x + cw - 3:.1f}" y="{y + 10}" text-anchor="end" style="font-size:10px">◆</text>')
        if c["stuck"]:
            out.append(f'<text x="{x + 3:.1f}" y="{y + 10}" style="font-size:10px;fill:var(--a2)">↻</text>')
        if cw >= 26 and c["calls"]:
            dark = op > 0.55
            out.append(f'<text x="{x + cw / 2:.1f}" y="{y + ch - 6}" text-anchor="middle" '
                       f'style="font-size:9px;{"fill:#fff" if dark else ""};pointer-events:none">{c["calls"]}</text>')
    out.append("</svg>")
    key = [('<b style="color:var(--a1)">▮</b>', "darker: more calls (per cell)"), ('<b style="color:var(--sc)">▁</b>', "a check failed"),
           ('<b style="color:var(--sg)">◆</b>', "a check started passing"), ('<b style="color:var(--a2)">↻</b>', "a repeating burst began")]
    return "".join(out) + legend([], key)


# ------------------------------------------------------------------ chapters
def _mixbar(mix: dict, w: int = 90, h: int = 9) -> str:
    total = sum(mix.values()) or 1
    x, parts = 0.0, []
    for act in _ORDER:
        n = mix.get(act)
        if not n:
            continue
        ww = w * n / total
        parts.append(f'<rect class="{ACTIVITIES.get(act, ACTIVITIES["other"])[0]}" x="{x:.1f}" y="0" width="{ww:.1f}" '
                     f'height="{h}"><title>{n} {ACTIVITIES.get(act, ACTIVITIES["other"])[2]}</title></rect>')
        x += ww
    return f'<svg class="viz soft" viewBox="0 0 {w} {h}" width="{w}" height="{h}" style="display:inline-block;opacity:.7">{"".join(parts)}</svg>'


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


def chapters_table(r: dict, *, base: str = "", most: int = 80) -> str:
    """The run's story as rows: a loop is one row, filler is one row, idle a divider."""
    sa, span = r.get("started_at"), r["span_s"]
    rows = []
    for p in phases(r)[:most]:
        if p["kind"] == "idle":
            rows.append(f'<tr class="idle"><td colspan="7" class="muted" style="text-align:center">— {e(dur(p["seconds"]))} '
                        f'idle —</td></tr>')
            continue
        a, b = p["bursts"]
        mine = r["bursts"][a - 1:b]
        mix: Counter = Counter()
        for x in mine:
            mix.update(x["mix"])
        passed = sum(x["checks"]["passed"] for x in mine)
        failed = sum(x["checks"]["failed"] for x in mine)
        if p["kind"] == "loop":
            badge = '<span class="badge bad">↻ loop</span>'
            what = (f'the same calls {p["count"]}× in a row'
                    + (f', across {p["sessions"]} sessions and the idle between' if p.get("sessions", 1) > 1 else "")
                    + (f'; <code>{e(p["failing"][:70])}</code> kept failing' if p["failing"] else ""))
        elif p["kind"] == "filler":
            badge = '<span class="badge idle">filler</span>'
            what = f'{b - a + 1} burst(s) of reading and editing; no check changed'
        else:
            fill, glyph, desc = STATUS.get(p["status"], STATUS["working"])
            tone = "ok" if p["status"] in ("progress", "done") else "bad" if p["status"] in ("stuck", "regressed", "failing") else ""
            badge = f'<span class="badge {tone}">{e(glyph)} {e(p["status"])}</span>'
            ev = next((x for x in reversed(mine) if x["note"]), mine[-1])
            what = e(ev["note"] or desc)
        label = f"burst {a}" if a == b else f"bursts {a}–{b}"
        rows.append(f'<tr><td><a href="{e(base)}&amp;burst={a}#p-long">{e(label)}</a></td>'
                    f'<td class="muted">{e(when(p["from"], sa, span))}</td><td class="n">{e(dur(p["to"] - p["from"]))}</td>'
                    f'<td class="n">{p["calls"]:,}</td><td>{_mixbar(dict(mix))}</td>'
                    f'<td class="n">{"<span class=ok>✓" + str(passed) + "</span> " if passed else ""}'
                    f'{"<span class=bad>✗" + str(failed) + "</span>" if failed else ""}</td>'
                    f'<td>{badge} {what}</td></tr>')
    more = len(phases(r)) - most
    return ('<table class="chapters"><tr><th>phase</th><th>when</th><th class="n">took</th><th class="n">calls</th>'
            '<th>what it did</th><th class="n">checks</th><th>how it went</th></tr>' + "".join(rows) + "</table>"
            + (f'<p class="muted">{more} more phase(s).</p>' if more > 0 else ""))


# ------------------------------------------------------------- every call
def burst_calls(win: dict, r: dict, *, width: int = 980, page: int = 0, per_page: int = 600, base: str = "") -> str:
    """Every call in a window: on its clock, a lane per activity; then in order,
    each call the same width, with a line from each to its moment on the clock."""
    steps = win["steps"]
    if not steps:
        return '<p class="muted">No steps in this stretch.</p>'
    pages = max(1, math.ceil(len(steps) / per_page))
    page = min(max(0, page), pages - 1)
    shown = steps[page * per_page:(page + 1) * per_page]
    t0, t1 = min(x["start"] for x in shown), max(x["end"] for x in shown)
    t1 = max(t1, t0 + 1.0)
    left, right = 70, 12
    x0, x1 = left, width - right
    acts = [a for a in _ORDER if any(x["activity"] == a for x in shown)]
    lane_h = 16
    top = 24
    clock_h = lane_h * len(acts)
    y_seq = top + clock_h + 58
    seq_h = 22
    height = y_seq + seq_h + 40
    sa, span = r.get("started_at"), r["span_s"]

    def tx(t: float) -> float:
        return x0 + (x1 - x0) * (t - t0) / (t1 - t0)
    cw = (x1 - x0) / len(shown)
    out = [f'<svg class="viz soft" viewBox="0 0 {width} {height}" width="{width}" role="img" '
           f'aria-label="{len(shown)} calls, on their clock and in order">']
    # the clock's ticks
    step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 21600)
                 if s * (x1 - x0) / (t1 - t0) >= 70), 21600)
    k = math.ceil(t0 / step) * step
    while k <= t1:
        x = tx(k)
        out.append(f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{top - 4}" y2="{top + clock_h}"/>'
                   f'<text x="{x + 2:.1f}" y="{top - 8}" style="font-size:9.5px">{e(when(k, sa, max(span, 3601)) if step >= 60 else "+" + dur(k - t0))}</text>')
        k += step
    for i, a in enumerate(acts):
        cls, glyph, label = ACTIVITIES.get(a, ACTIVITIES["other"])
        out.append(f'<text x="4" y="{top + i * lane_h + 12}" style="font-size:10px">{e(glyph)} {e(label)}</text>'
                   f'<line class="grid" x1="{x0}" x2="{x1}" y1="{top + (i + 1) * lane_h - 1}" y2="{top + (i + 1) * lane_h - 1}"/>')
    row = {a: i for i, a in enumerate(acts)}
    out.append(f'<text x="4" y="{y_seq + 15}" style="font-size:10px">in order</text>')
    connectors = len(shown) <= 400
    # the repeated failing calls in a row, bracketed over the sequence
    streak_from, last_sig = None, None
    brackets = []
    for k2, x in enumerate(shown + [None]):
        sig = (x["name"], x.get("call")) if x else None
        bad = bool(x and (x["error"] or x["check"] is False))
        if x is not None and sig == last_sig and bad:
            continue
        if streak_from is not None and k2 - streak_from >= 3:
            brackets.append((streak_from, k2 - 1))
        streak_from, last_sig = (k2, sig) if bad else (None, None)
    for k2, x in enumerate(shown):
        cls, glyph, label = ACTIVITIES.get(x["activity"], ACTIVITIES["other"])
        xa = tx(x["start"])
        w = max(1.5, tx(x["end"]) - xa)
        y = top + row[x["activity"]] * lane_h + 2
        bad = x["error"] or x["check"] is False
        tip = (f'#{x["index"]} {x["name"]} · {when(x["start"], sa, max(span, 3601))} · {x["latency_s"]:.1f}s · '
               f'{x["tokens"]:,} tokens' + (f' · {x["call"]}' if x.get("call") else "")
               + (" · check passed" if x["check"] is True else " · check failed" if x["check"] is False else "")
               + (" · error" if x["error"] else "") + (f' → {x["said"]}' if x.get("said") else ""))
        out.append(f'<a href="#s{x["index"]}"><rect class="{cls} hot" x="{xa:.1f}" y="{y}" width="{w:.1f}" height="{lane_h - 5}" '
                   f'rx="2"{_BAD_EDGE if bad else ""}><title>{e(tip)}</title></rect></a>')
        sx = x0 + k2 * cw
        if connectors:
            out.append(f'<line x1="{xa + w / 2:.1f}" y1="{top + clock_h + 2}" x2="{sx + cw / 2:.1f}" y2="{y_seq - 2}" '
                       f'style="stroke:var(--ink2);stroke-opacity:.18"/>')
        out.append(f'<a href="#s{x["index"]}"><rect class="{cls} hot" x="{sx:.2f}" y="{y_seq}" width="{max(0.6, cw - (0.5 if cw > 3 else 0)):.2f}" '
                   f'height="{seq_h}"><title>{e(tip)}</title></rect></a>')
        if bad:
            out.append(f'<rect x="{sx:.2f}" y="{y_seq + seq_h + 1}" width="{max(0.6, cw - .5):.2f}" height="3" style="fill:var(--sc)"/>')
        elif x["check"] is True:
            out.append(f'<rect x="{sx:.2f}" y="{y_seq + seq_h + 1}" width="{max(0.6, cw - .5):.2f}" height="3" style="fill:var(--sg)"/>')
        if cw >= 11:
            out.append(f'<text class="g{" dk" if cls in _DARK_GLYPH else ""}" x="{sx + cw / 2:.1f}" y="{y_seq + 15}" '
                       f'text-anchor="middle">{e(glyph)}</text>')
    for a, b in brackets:
        xa, xb = x0 + a * cw, x0 + (b + 1) * cw
        out.append(f'<path d="M{xa:.1f},{y_seq - 4} v-5 H{xb:.1f} v5" style="fill:none;stroke:var(--sc);stroke-width:1.5"/>'
                   f'<text class="bad" x="{(xa + xb) / 2:.1f}" y="{y_seq - 12}" text-anchor="middle" style="font-size:10px">'
                   f'↻ {b - a + 1}× the same failing call</text>')
    out.append(f'<text x="{x0}" y="{y_seq + seq_h + 18}" style="font-size:10px">each call the same width: '
               f'{len(shown)} call(s); the lines show where the clock ran fast (fanned in) or slow (fanned out)</text>')
    out.append("</svg>")
    nav = ""
    if pages > 1:
        links = []
        for p in range(pages):
            a = p * per_page
            links.append(f'<a href="{e(base)}&amp;page={p}#p-long"{" class=on" if p == page else ""}>'
                         f'{a + 1}–{min(len(steps), a + per_page)}</a>')
        nav = f'<div class="filters"><span class="muted">calls</span>{"".join(links)}</div>'
    used = sorted({x["activity"] for x in shown})
    return nav + "".join(out) + legend(used, [('<b style="color:var(--sc)">▁</b>', "error or failed check"),
                                              ('<b style="color:var(--sg)">▁</b>', "check passed")])


# ------------------------------------------------------------------- pace
def pace_chart(r: dict, *, width: int = 980) -> str:
    rows = (r.get("pace") or {}).get("rows") or []
    if len(rows) < 3:
        return ""
    bucket = r["pace"]["bucket_s"]
    left, right, h, gap = 70, 12, 46, 16
    x0, x1 = left, width - right
    span = max(r["span_s"], rows[-1]["to"])

    def tx(t):
        return x0 + (x1 - x0) * t / span
    charts = (("calls", f"calls / {dur(bucket)}", lambda p: p["calls"], "bar"),
              ("sec", "seconds per call", lambda p: p["sec_per_call"], "dot"),
              ("tok", "tokens per step", lambda p: p["tokens_per_step"], "dot"))
    height = len(charts) * (h + gap) + 16
    out = [f'<svg class="viz soft" viewBox="0 0 {width} {height}" width="{width}" role="img" '
           f'aria-label="pace over the run: calls, seconds per call, tokens per step">']
    for k, (key, label, get, kind) in enumerate(charts):
        y0 = 6 + k * (h + gap)
        vals = [get(p) for p in rows if get(p) is not None]
        top = max(vals) if vals else 1
        out.append(f'<text x="4" y="{y0 + 12}" style="font-size:10px">{e(label)}</text>'
                   f'<text x="4" y="{y0 + 24}" style="font-size:9.5px">max {top:,.1f}</text>'
                   f'<line class="grid" x1="{x0}" x2="{x1}" y1="{y0 + h}" y2="{y0 + h}"/>')
        pts = []
        for p in rows:
            v = get(p)
            if v is None:
                continue
            xa, xb = tx(p["from"]), tx(p["to"])
            hh = h * v / (top or 1)
            tip = f'{when(p["from"], r.get("started_at"), r["span_s"])}: {label} {v:,.1f}'
            if kind == "bar":
                out.append(f'<rect class="bar" x="{xa:.1f}" y="{y0 + h - hh:.1f}" width="{max(1.0, xb - xa - 1):.1f}" '
                           f'height="{hh:.1f}"><title>{e(tip)}; {p["failed"]} failed check(s)</title></rect>')
                if p["failed"]:
                    out.append(f'<rect x="{xa:.1f}" y="{y0 + h + 1}" width="{max(1.0, xb - xa - 1):.1f}" height="3" '
                               f'style="fill:var(--sc)"/>')
            else:
                pts.append(f"{(xa + xb) / 2:.1f},{y0 + h - hh:.1f}")
                out.append(f'<circle cx="{(xa + xb) / 2:.1f}" cy="{y0 + h - hh:.1f}" r="2.5" style="fill:var(--a1)">'
                           f'<title>{e(tip)}</title></circle>')
        if len(pts) > 1:
            out.append(f'<polyline points="{" ".join(pts)}" style="fill:none;stroke:var(--a1);stroke-width:1.5;'
                       f'stroke-opacity:.6"/>')
    out.append("</svg>")
    trend = (r["pace"].get("trend") or {}).get("sentence")
    return (f'<p class="muted">{e(trend)}</p>' if trend else "") + "".join(out)


# ---------------------------------------------------------------- treemap
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


def phase_treemap(r: dict, items: List[dict], *, width: int = 980, height: int = 260, base: str = "") -> str:
    """Where the working time went: a box per phase, a tile per tool, area by seconds."""
    allp = phases(r)
    keys = [k for k, p in enumerate(allp) if p["kind"] != "idle"]  # the lens numbers phases with the idle among them
    ph = [allp[k] for k in keys]
    if not ph:
        return ""
    secs = []
    tools_of = []
    by_index = {x["index"]: x for x in items}
    for p in ph:
        tc: Dict[str, list] = {}
        for i in range(p["first"], p["last"] + 1):
            x = by_index.get(i)
            if x is None or x["latency_s"] <= 0:
                continue
            t = tc.setdefault(x["name"], [0.0, 0, 0, Counter()])
            t[0] += x["latency_s"]
            t[1] += 1
            t[2] += int(x["error"] or x["check"] is False)
            t[3][x["activity"]] += 1
        tools_of.append(sorted(tc.items(), key=lambda kv: -kv[1][0]))
        secs.append(sum(v[0] for _, v in tc.items()))
    order = sorted(range(len(ph)), key=lambda k: -secs[k])
    rects = _squarify([secs[k] for k in order], 0, 16, width, height)
    sa, span = r.get("started_at"), r["span_s"]
    out = [f'<svg class="viz soft treemap" viewBox="0 0 {width} {height + 18}" width="{width}" role="img" '
           f'aria-label="where the working time went: a box per phase, a tile per tool, area by seconds">'
           f'<text x="0" y="11" style="font-size:10px">area: seconds a tool ran, inside each phase '
           f'({dur(sum(secs))} in all)</text>']
    for k, rc in zip(order, rects):
        if rc is None:
            continue
        x, y, w, h = rc
        p = ph[k]
        a, b = p["bursts"]
        name = (f"loop {a}–{b}" if p["kind"] == "loop" else f"filler {a}–{b}" if p["kind"] == "filler"
                else f"burst {a}")
        trs = tools_of[k]
        inner = _squarify([v[0] for _, v in trs], x + 1, y + (14 if h > 40 and w > 50 else 1), max(1, w - 2),
                          max(1, h - (15 if h > 40 and w > 50 else 2)))
        out.append(f'<g data-phase="{keys[k]}">')
        for (tool, (ts, n, bad, acts)), irc in zip(trs, inner):
            if irc is None:
                continue
            ix, iy, iw, ih = irc
            act = acts.most_common(1)[0][0]
            cls = ACTIVITIES.get(act, ACTIVITIES["other"])[0]
            share = bad / n if n else 0
            out.append(f'<rect class="{cls}" x="{ix + .5:.1f}" y="{iy + .5:.1f}" width="{max(.5, iw - 1):.1f}" '
                       f'height="{max(.5, ih - 1):.1f}" style="opacity:{".55" if act == "think" else ".9"}'
                       f'{";stroke:var(--sc);stroke-width:2" if share >= 0.5 else ""}"><title>{e(name)} · {e(tool)}: '
                       f'{n} call(s), {e(dur(ts))}' + (f', {bad} failed' if bad else "") + '</title></rect>')
            if iw > 44 and ih > 14:
                out.append(f'<text class="g{" dk" if cls in _DARK_GLYPH else ""}" x="{ix + 4:.1f}" y="{iy + 12:.1f}" '
                           f'style="font-size:9.5px">{e(_fit(f"{tool} {n}×", iw - 6, 5.8))}</text>')
        tone = "var(--sc)" if p["kind"] == "loop" or p.get("status") in ("stuck", "regressed") else "var(--ink2)"
        out.append(f'<a href="{e(base)}&amp;burst={a}#p-long"><rect class="hot" x="{x + .5:.1f}" y="{y + .5:.1f}" '
                   f'width="{max(1, w - 1):.1f}" height="{max(1, h - 1):.1f}" style="fill:transparent;stroke:{tone};'
                   f'stroke-width:{2 if tone != "var(--ink2)" else 1}"><title>{e(name)}: {e(when(p["from"], sa, span))}, '
                   f'{e(dur(secs[k]))} of tool time, {p["calls"]:,} calls — open it</title></rect></a>')
        if h > 40 and w > 50:
            out.append(f'<text class="lab" x="{x + 4:.1f}" y="{y + 11:.1f}" style="font-size:10.5px;font-weight:700;'
                       f'fill:{tone}">{e(_fit(name + " · " + dur(secs[k]), w - 8, 6.0))}</text>')
        out.append("</g>")
    out.append("</svg>")
    return "".join(out) + legend(sorted({x["activity"] for x in items}),
                                 [('<b style="color:var(--sc)">▢</b>', "a loop, or a tool failing half its calls or more")])


# ------------------------------------------------------------- two long runs
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


def diff_timeline(da: dict, db: dict, ia: List[dict], ib: List[dict], *, names=("A", "B"), width: int = 980) -> str:
    """Two long runs cut at the checkpoints they share (the same check starting
    to pass, in the same order), their tool use mirrored: A above, B below."""
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
            return {"n": len(got), "mix": Counter(x["activity"] for x in got),
                    "fail": sum(1 for x in got if x["error"] or x["check"] is False),
                    "secs": sum(x["latency_s"] for x in got), "wall": max(0.0, min(t1, max([x["end"] for x in got] or [t0])) - t0)}
        segs.append({"a": take(ia, pa[1], qa[1]), "b": take(ib, pb[1], qb[1]),
                     "label": (qa[0] or "end").split(":", 1)[-1] if qa[0] != "end" else "the end"})
    if not segs:
        return ""
    left, right = 70, 12
    x0, x1 = left, width - right
    wts = [math.sqrt(max(s["a"]["n"], s["b"]["n"], 1)) for s in segs]
    tot = sum(wts)
    half = 70
    mid = 42 + half
    height = mid + half + 46
    most = max(max(s["a"]["n"], s["b"]["n"]) for s in segs) or 1
    out = [f'<svg class="viz soft" viewBox="0 0 {width} {height}" width="{width}" role="img" '
           f'aria-label="two runs cut at {len(pairs)} shared checkpoint(s); calls above for {e(names[0])}, below for {e(names[1])}">']
    out.append(f'<text x="4" y="{mid - half / 2}" style="font-size:10px">{e(_fit(names[0], 64))}</text>'
               f'<text x="4" y="{mid + half / 2 + 8}" style="font-size:10px">{e(_fit(names[1], 64))}</text>'
               f'<line x1="{x0}" x2="{x1}" y1="{mid}" y2="{mid}" style="stroke:var(--ink2)"/>')
    x = float(x0)
    worst, worst_k = 0, None
    for k, (s, wt) in enumerate(zip(segs, wts)):
        w = (x1 - x0) * wt / tot
        for side, sign in (("a", -1), ("b", 1)):
            d = s[side]
            yy = mid
            for act in _ORDER:
                n = d["mix"].get(act)
                if not n:
                    continue
                # height is the square root of the steps, so a stretch of 20 still shows beside one of 2,000
                hh = half * (math.sqrt(d["n"]) / math.sqrt(most)) * n / d["n"]
                y = yy - hh if sign < 0 else yy
                out.append(f'<rect class="{ACTIVITIES.get(act, ACTIVITIES["other"])[0]}" x="{x + 1:.1f}" y="{y:.1f}" '
                           f'width="{max(1.0, w - 2):.1f}" height="{hh:.1f}"><title>{e(names[0 if side == "a" else 1])}: '
                           f'{n} {e(ACTIVITIES.get(act, ACTIVITIES["other"])[2])} step(s) before {e(s["label"])}</title></rect>')
                yy = yy - hh if sign < 0 else yy + hh
            if d["fail"]:
                tall = half * math.sqrt(d["n"] / most)
                fy = (mid - tall - 5) if sign < 0 else (mid + tall + 2)
                out.append(f'<rect x="{x + 1:.1f}" y="{fy:.1f}" width="{max(1.0, w - 2):.1f}" height="3" style="fill:var(--sc)">'
                           f'<title>{d["fail"]} failed</title></rect>')
        delta = s["a"]["n"] - s["b"]["n"]
        if abs(delta) > worst:
            worst, worst_k = abs(delta), k
        if w > 60 and abs(delta) >= max(10, 0.3 * max(s["a"]["n"], s["b"]["n"])):
            out.append(f'<text x="{x + w / 2:.1f}" y="{mid + 4 + (half + 12) * (-1 if delta > 0 else 1):.1f}" text-anchor="middle" '
                       f'style="font-size:10px;font-weight:700">{"A" if delta > 0 else "B"} +{abs(delta):,}</text>')
        out.append(f'<line class="grid" x1="{x + w:.1f}" x2="{x + w:.1f}" y1="{mid - half - 4}" y2="{mid + half + 4}"/>')
        if k < len(segs) - 1:
            out.append(f'<text x="{x + w:.1f}" y="{height - 8}" text-anchor="middle" style="font-size:9px">◆'
                       f'<title>checkpoint {k + 1}: {e(s["label"])} starts passing in both</title></text>')
        x += w
    out.append(f'<text x="{x0}" y="16" style="font-size:10px">cut at {len(pairs)} checkpoint(s) both runs reached, '
               f'in the same order; width and height grow with the square root of the steps between them</text>')
    out.append("</svg>")
    sentence = ""
    if worst_k is not None:
        s = segs[worst_k]
        before = "the first checkpoint" if worst_k == 0 else f"checkpoint {worst_k}"
        sentence = (f'<p class="note">They part most between {e(before)} and <code>{e(s["label"][:70])}</code>: '
                    f'{e(names[0])} made {s["a"]["n"]:,} step(s) over {e(dur(s["a"]["wall"]))}, '
                    f'{e(names[1])} {s["b"]["n"]:,} over {e(dur(s["b"]["wall"]))}.</p>')
    return sentence + "".join(out) + legend(sorted({x["activity"] for x in ia + ib}),
                                            [('<b>◆</b>', "a checkpoint both reached"),
                                             ('<b style="color:var(--sc)">▁</b>', "failed")])


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
