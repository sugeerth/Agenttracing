"""The hub's pages, as HTML strings. No scripts, no external files.

Every value that reaches a page goes through :func:`e` (escaped), every
form that changes state carries its token, and the look is one set of
colour tokens with a dark variant. The full AgentDiff report a run links
to is the page the run wrote; the hub only frames it.
"""

from __future__ import annotations

import html
import time
from typing import Iterable, List, Optional

from .catalog import Entry

__all__ = ["layout", "login_page", "runs_page", "duel_page", "report_page", "telemetry_page", "message_page"]


def e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


_CSS = """
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1d1d1b;--soft:#5d5d58;--line:#e3e2dc;--accent:#2f5bd3;
--good:#1e7a46;--bad:#b3261e;--warn:#9a6700;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--panel:#1d1d1b;--ink:#ecebe6;--soft:#a3a29c;
--line:#33322f;--accent:#8ab0ff;--good:#5cc98a;--bad:#ff8a80;--warn:#e3b341}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--accent)}header{display:flex;align-items:center;gap:16px;padding:12px 20px;
border-bottom:1px solid var(--line);background:var(--panel)}header .brand{font-weight:650;letter-spacing:.2px}
header nav{flex:1;display:flex;gap:14px}header form{margin:0}
main{max-width:1080px;margin:0 auto;padding:22px 16px}h1{font-size:20px;margin:4px 0 14px}
h2{font-size:15px;margin:22px 0 8px;color:var(--soft);font-weight:600;text-transform:uppercase;letter-spacing:.6px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:0 0 10px}
.row{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}.kind{font:600 11px var(--mono);
text-transform:uppercase;letter-spacing:.6px;padding:2px 7px;border-radius:999px;border:1px solid var(--line);color:var(--soft)}
.muted{color:var(--soft)}.mono{font-family:var(--mono);font-size:13px}.ok{color:var(--good)}.bad{color:var(--bad)}
.warn{color:var(--warn)}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 8px;
border-bottom:1px solid var(--line);font-size:14px}th{color:var(--soft);font-weight:600}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}button,input{font:inherit}
input[type=text],input[type=password]{width:100%;padding:9px 10px;border:1px solid var(--line);border-radius:8px;
background:var(--bg);color:var(--ink)}button{padding:8px 14px;border-radius:8px;border:1px solid var(--accent);
background:var(--accent);color:var(--panel);cursor:pointer}button.link{background:none;border:none;color:var(--accent);
padding:0}.login{max-width:360px;margin:9vh auto}.login label{display:block;margin:12px 0 4px;color:var(--soft)}
.note{border-left:3px solid var(--warn);padding:6px 10px;margin:8px 0;background:var(--panel)}
.error{border-left-color:var(--bad)}code{font-family:var(--mono);font-size:13px}
pre{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px;overflow:auto}
svg text{fill:var(--soft);font:11px var(--mono)}svg .lane{stroke:var(--line)}
svg .hop{fill:var(--accent)}svg .hop.err{fill:var(--bad)}svg text.on{fill:var(--panel)}
@media (max-width:640px){header{flex-wrap:wrap}td,th{font-size:13px;padding:5px}}
"""


def layout(title: str, body: str, *, brand: str, user: Optional[str] = None, csrf: Optional[str] = None) -> str:
    top = ""
    if user:
        top = (f'<nav><a href="/">Runs</a></nav><span class="muted">{e(user)}</span>'
               f'<form method="post" action="/logout"><input type="hidden" name="csrf" value="{e(csrf)}">'
               f'<button class="link" type="submit">Sign out</button></form>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{e(title)} · {e(brand)}</title><style>{_CSS}</style></head><body>'
            f'<header><span class="brand">{e(brand)}</span>{top}</header><main>{body}</main></body></html>')


def login_page(*, brand: str, token: str, error: Optional[str], demo: Optional[tuple], next_path: str) -> str:
    hint = ""
    if demo:
        hint = (f'<p class="note">Demo account: <code>{e(demo[0])}</code> / <code>{e(demo[1])}</code>. '
                f'On by default on this machine only; turn it off with <code>--no-demo</code>.</p>')
    err = f'<p class="note error">{e(error)}</p>' if error else ""
    body = (f'<div class="login card"><h1>Sign in</h1>{err}{hint}'
            f'<form method="post" action="/login"><input type="hidden" name="csrf" value="{e(token)}">'
            f'<input type="hidden" name="next" value="{e(next_path)}">'
            f'<label for="u">User</label><input id="u" type="text" name="user" autocomplete="username" required autofocus>'
            f'<label for="p">Password</label><input id="p" type="password" name="password" '
            f'autocomplete="current-password" required>'
            f'<p><button type="submit">Sign in</button></p></form></div>')
    return layout("Sign in", body, brand=brand)


def _ago(t: float) -> str:
    s = max(0, time.time() - t)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{int(s // n)}{unit} ago"
    return "just now"


def _score_line(entry: Entry) -> str:
    rows = entry.summary.get("rows") or []
    parts = []
    for r in rows:
        if r.get("graded"):
            cls = "ok" if r["passed"] == r["graded"] else ("bad" if not r["passed"] else "warn")
            parts.append(f'{e(r["agent"])} <span class="{cls}">{e(r["passed"])}/{e(r["graded"])}</span>')
        else:
            parts.append(f'{e(r["agent"])} <span class="muted">ungraded</span>')
    return " · ".join(parts)


def runs_page(*, brand: str, user: str, csrf: str, entries: List[Entry], ingest: dict) -> str:
    cards = []
    for x in entries:
        if x.kind == "duel":
            detail = _score_line(x)
        elif x.kind == "telemetry":
            s = x.summary
            detail = (f'{e(s.get("hops"))} hops across {e(len(s.get("nodes") or []))} process(es), '
                      f'{e(s.get("errors"))} not ok')
        else:
            detail = e(", ".join(map(str, x.summary.get("agents") or [])))
        cards.append(f'<div class="card"><div class="row"><span class="kind">{e(x.kind)}</span>'
                     f'<a href="/runs/{e(x.id)}"><strong>{e(x.title)}</strong></a>'
                     f'<span class="muted">{e(_ago(x.updated))}</span></div>'
                     f'<div class="muted">{detail}</div></div>')
    if not cards:
        cards.append('<div class="card muted">No runs here yet. In a project: <code>agentdiff fix</code> or '
                     '<code>agentdiff "the task"</code>, then reload.</div>')
    body = (f'<h1>Runs</h1>{"".join(cards)}<h2>Send telemetry here</h2><div class="card">'
            f'<p class="muted">Agents traced in-band post their vector to this hub:</p>'
            f'<pre>export AGENTDIFF_HUB={e(ingest["url"])}\nexport AGENTDIFF_HUB_TOKEN={e(ingest["token"])}\n'
            f'agentdiff telemetry send "$AGENTDIFF_INT"</pre></div>')
    return layout("Runs", body, brand=brand, user=user, csrf=csrf)


def duel_page(*, brand: str, user: str, csrf: str, entry: Entry) -> str:
    s = entry.summary
    head = "".join(f'<th class="n">{h}</th>' for h in ("passed", "median tokens", "cost", "median time"))
    body_rows = []
    for r in s.get("rows") or []:
        cost = f'${r["cost_usd"]:.3f}' if isinstance(r.get("cost_usd"), (int, float)) else "not reported"
        tok = f'{int(r["tokens"]):,}' if isinstance(r.get("tokens"), (int, float)) else "—"
        wall = f'{r["wall_s"]:.0f}s' if isinstance(r.get("wall_s"), (int, float)) else "—"
        passed = f'{r["passed"]} of {r["graded"]}' if r.get("graded") else "ungraded"
        body_rows.append(f'<tr><td>{e(r["agent"])}</td><td class="n">{e(passed)}</td><td class="n">{e(tok)}</td>'
                         f'<td class="n">{e(cost)}</td><td class="n">{e(wall)}</td></tr>')
    notes = []
    if s.get("already_passing"):
        notes.append(f'The check passed before any work on {e(", ".join(s["already_passing"]))}: a pass there shows nothing.')
    if s.get("unequal"):
        notes.append("Not equal between them: " + e(", ".join(s["unequal"])) + ".")
    winners = [r["agent"] for r in s.get("rows") or [] if r.get("graded") and r.get("passed")]
    keep = " or ".join(f"<code>agentdiff apply {e(a)}</code>" for a in winners)
    body = (f'<h1>{e(entry.title)}</h1><div class="card"><table><tr><th>agent</th>{head}</tr>'
            f'{"".join(body_rows)}</table></div>'
            + "".join(f'<p class="note">{n}</p>' for n in notes)
            + f'<div class="card"><p>{e(s.get("narrative"))}</p>'
            + (f'<p><a href="/runs/{e(entry.id)}/page">Open the full report</a></p>' if entry.page else "")
            + f'</div><h2>From here</h2><div class="card"><p class="mono">cd {e(entry.path.parent)}</p>'
            + (f'<p>Keep a change: {keep}</p>' if keep else "")
            + '<p>More runs of the same comparison: <code>agentdiff again 3</code></p></div>')
    return layout(entry.title, body, brand=brand, user=user, csrf=csrf)


def report_page(*, brand: str, user: str, csrf: str, entry: Entry) -> str:
    body = (f'<h1>{e(entry.title)}</h1><div class="card"><p class="muted">{e(entry.path)}</p>'
            f'<p><a href="/runs/{e(entry.id)}/page">Open the report</a></p></div>')
    return layout(entry.title, body, brand=brand, user=user, csrf=csrf)


def _clock(t: float, span: float) -> str:
    """A tick label in the unit the span is measured in."""
    if span < 1:
        return f"{t * 1000:.0f}ms" if span >= 0.01 else f"{t * 1000:.1f}ms"
    if span < 120:
        return f"{t:.1f}s" if span < 10 else f"{t:.0f}s"
    return f"{t / 60:.1f}m"


def _timeline(rows: List[dict], span: float) -> str:
    nodes = sorted({r.get("node") or "?" for r in rows})
    lane_h, left, width = 26, 170, 960
    height = 24 + lane_h * max(1, len(nodes))
    span = span or 1.0
    x = lambda t: left + (width - left - 10) * (t / span)  # noqa: E731
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
             f'aria-label="hops on one clock, one lane per process">']
    for i in range(5):
        t = span * i / 4
        anchor = "start" if i == 0 else "end" if i == 4 else "middle"
        parts.append(f'<text x="{x(t):.1f}" y="12" text-anchor="{anchor}">{e(_clock(t, span))}</text>')
    for li, node in enumerate(nodes):
        y = 22 + li * lane_h
        parts.append(f'<line class="lane" x1="{left}" x2="{width - 10}" y1="{y + lane_h - 4}" y2="{y + lane_h - 4}"/>')
        parts.append(f'<text x="4" y="{y + 15}">{e(node[:24])}</text>')
        for r in rows:
            if (r.get("node") or "?") != node:
                continue
            x0 = x(r.get("start") or 0.0)
            w = max(2.0, x((r.get("start") or 0.0) + (r.get("latency") or 0.0)) - x0)
            cls = "hop" + ("" if r.get("status") in (None, "ok") else " err")
            tip = f'{r.get("tool")} · {r.get("status")} · {(r.get("latency") or 0):.3f}s'
            parts.append(f'<rect class="{cls}" x="{x0:.1f}" y="{y + 3}" width="{w:.1f}" height="{lane_h - 10}" rx="3">'
                         f'<title>{e(tip)}</title></rect>')
            label = str(r.get("tool") or "")
            if w > 7 * len(label) + 8:
                # the tool's name on its bar, where it fits
                parts.append(f'<text class="on" x="{x0 + 5:.1f}" y="{y + 15}">{e(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def telemetry_page(*, brand: str, user: str, csrf: str, entry: Entry, rows: List[dict], info: dict) -> str:
    cols = [c for c in ("start", "latency", "tool", "kind", "status", "bytes_in", "bytes_out", "tokens_in",
                        "tokens_out", "node") if any(c in r for r in rows)]
    def cell(r, c):
        v = r.get(c)
        if isinstance(v, float):
            return f"{v:.3f}"
        return v
    table = ("<table><tr>" + "".join(f"<th>{e(c)}</th>" for c in cols) + "</tr>"
             + "".join("<tr>" + "".join(
                 f'<td class="{"bad" if c == "status" and r.get(c) not in (None, "ok") else ""}">{e(cell(r, c))}</td>'
                 for c in cols) + "</tr>" for r in rows) + "</table>")
    notes = []
    if info.get("dropped"):
        notes.append(f'{info["dropped"]} hop(s) were refused at the hop budget: the path is incomplete.')
    if info.get("clamped"):
        notes.append("Some values were too large for a 32-bit word and were clamped.")
    span_s = "%.3f" % info["span_s"]
    body = (f'<h1>{e(entry.title)}</h1><p class="muted">{e(info["hops"])} hops across '
            f'{e(len(info["nodes"]))} process(es) in {e(span_s)}s · {e(info["errors"])} not ok · '
            f'trace <span class="mono">{e(info["trace_id"])}</span></p>'
            + "".join(f'<p class="note">{e(n)}</p>' for n in notes)
            + f'<div class="card">{_timeline(rows, info["span_s"])}</div><div class="card">{table}</div>'
            f'<p class="muted">In-band telemetry carries sizes, times and outcomes, never what a tool read or returned. '
            f'As a trace for every other command: <code>agentdiff batch {e(entry.path / "traces")}</code></p>')
    return layout(entry.title, body, brand=brand, user=user, csrf=csrf)


def message_page(*, brand: str, title: str, text: str, user: Optional[str] = None, csrf: Optional[str] = None) -> str:
    return layout(title, f'<h1>{e(title)}</h1><div class="card"><p>{e(text)}</p></div>', brand=brand,
                  user=user, csrf=csrf)
