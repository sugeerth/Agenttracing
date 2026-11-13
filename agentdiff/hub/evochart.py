"""The evolution, run by run: every task, every arm of every generation, every run.

Rows are the tasks. Columns are the arms, grouped by generation: the
harness as it was, the change it tested, the confirmation runs when a
weak win was run again, and at the end the held-out tasks under the agent
as it ships and as it ended. Each run is one square, passed or failed by
its own check, and links to its trace. Above each generation: the change
tried and what the counts made of it. Above each arm: the share of its
runs that passed. Beside a task the change moved: which way.

Drawn on the server as SVG, read from each arm's records: no script, the
hover is CSS and the browser's own title.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

__all__ = ["evolution_matrix", "EVM_CSS", "arms_of"]

EVM_CSS = """
.evmwrap{overflow-x:auto;margin:6px 0 4px;-webkit-overflow-scrolling:touch}
.evm{display:block;font:12px system-ui,sans-serif}
.evm .tk{fill:var(--ink)}.evm .soft{fill:var(--soft)}
.evm .gh{font-weight:650;font-size:13px;fill:var(--ink)}
.evm .what{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;fill:var(--ink)}
.evm .arm{font-size:11px;fill:var(--ink)}.evm .tally{font-size:11px;fill:var(--soft);font-variant-numeric:tabular-nums}
.evm .tk.hot{font-weight:600}.evm .livearm{fill:var(--accent);font-weight:600}.evm .movedbar{fill:var(--accent)}
.evm .band{fill:var(--chip);opacity:.75}
.evm .pending{fill:none;stroke:var(--soft);stroke-width:1;opacity:.6}
.evm .pass{fill:var(--good)}.evm .fail{fill:var(--bad)}.evm .none{fill:none;stroke:var(--line)}
.evm .bed{fill:var(--line);opacity:.55}.evm .share{fill:var(--accent)}
.evm .row .bg{fill:transparent}.evm .row:hover .bg{fill:var(--chip)}
.evm a rect{transition:none}.evm a:hover rect.pass,.evm a:hover rect.fail,.evm a:focus rect{stroke:var(--ink);stroke-width:1.5}
.evm .up{fill:var(--good)}.evm .down{fill:var(--bad)}
.evm .kept{fill:var(--good);font-weight:600}.evm .reverted{fill:var(--bad);font-weight:600}
.evm .sep{stroke:var(--line);stroke-width:1}
.evmkey{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:12px;color:var(--soft);margin:2px 0 10px}
.evmkey i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.evmkey i.p{background:var(--good)}.evmkey i.f{background:var(--bad)}
.evmkey b{font-weight:600;margin-right:4px}.evmkey b.u{color:var(--good)}.evmkey b.d{color:var(--bad)}
"""

SQ, GAP, ROW, LABEL_W, COL_PAD, HEAD, GROUP_MIN = 11, 2, 18, 214, 12, 86, 132


def e(v) -> str:
    return html.escape(str(v), quote=True)


def _cut(text: str, most: int) -> str:
    text = str(text)
    return text if len(text) <= most else text[: most - 1] + "…"


def _runs(arm_dir: Path) -> Dict[str, list]:
    """task -> [{run, passed, wall_s, trace}] for one arm, in run order."""
    out: Dict[str, list] = {}
    rec_dir = arm_dir / "records"
    if not rec_dir.is_dir():
        return out
    for f in sorted(rec_dir.glob("*.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if r.get("failed") or not r.get("task"):
            continue
        out.setdefault(r["task"], []).append({
            "run": r.get("run"), "agent": r.get("agent"), "passed": (r.get("check") or {}).get("passed"),
            "wall_s": r.get("wall_s"), "trace": arm_dir / r["trace"] if r.get("trace") else None})
    for runs in out.values():
        runs.sort(key=lambda x: (int(str(x["run"]).lstrip("r") or 0) if str(x["run"]).lstrip("r").isdigit() else 0,
                                 str(x["agent"])))
    return out


def arms_of(path: Path, data: dict, progress: Optional[dict] = None) -> List[dict]:
    """The columns, in order: each generation's arms as its lineage and the folders on disk say, then the
    held-out pair. A generation that carried the kept change's runs reads them from where they were made."""
    cols: List[dict] = []
    prev_kept: Optional[str] = None
    for g in data.get("lineage") or []:
        gen, v = g["generation"], (g.get("harness") or {}).get("version", 0)
        a = g.get("action") or {}
        t = a.get("test") or {}
        own = path / f"{gen}-h{v}"
        base = own if own.is_dir() else (path / prev_kept if prev_kept else own)
        cols.append({"gen": gen, "role": "as it was", "head": "kept" if base != own else "as it was",
                     "short": f"v{v}", "dir": base, "carried": base != own})
        prev_kept = None
        if a:
            cv = v + 1
            cols.append({"gen": gen, "role": "with the change", "head": "the change", "short": f"v{cv}",
                         "dir": path / f"{gen}-h{cv}",
                         "moved": {"up": set(t.get("wins") or []), "down": set(t.get("losses") or [])}})
            if t.get("confirm"):
                cols.append({"gen": gen, "role": "run again, as it was", "head": "re-run", "short": f"v{v}",
                             "dir": path / f"{gen}-h{v}-confirm", "again": True})
                cols.append({"gen": gen, "role": "run again, with the change", "head": "re-run", "short": f"v{cv}",
                             "dir": path / f"{gen}-h{cv}-confirm", "again": True})
            if t.get("verdict") == "kept":
                prev_kept = f"{gen}-h{cv}"
    h = data.get("holdout") or {}
    if h.get("version"):
        cols.append({"gen": "held out", "role": "as it ships", "head": "as it ships", "short": "v0",
                     "dir": path / "holdout-h0"})
        cols.append({"gen": "held out", "role": "as it ended", "head": "as it ended", "short": f"v{h['version']}",
                     "dir": path / f"holdout-h{h['version']}"})
    pr = progress or {}
    if pr.get("status") == "running" and pr.get("arm"):
        # the generation running now has no lineage yet: its arms are the folders it has made so far
        gen = str(pr["arm"]).split("-")[0]
        have = {c["dir"] for c in cols}
        made = [d for d in path.glob(f"{gen}-h*") if d.is_dir() and d not in have]
        if (path / pr["arm"]) not in have and (path / pr["arm"]) not in made:
            made.append(path / pr["arm"])

        def order(d: Path) -> tuple:
            name = d.name
            return (name.endswith("-confirm"), int(re.sub(r"\D", "", name.split("-h")[1]) or 0))
        made.sort(key=order)
        plain = [d for d in made if not d.name.endswith("-confirm")]
        low = order(plain[0])[1] if plain else None
        for d in made:
            again = d.name.endswith("-confirm")
            v = order(d)[1]
            is_base = v == low if low is not None else not again
            head = "re-run" if again else ("as it was" if is_base else "the change")
            role = ("run again, " if again else "") + ("as it was" if is_base else "with the change")
            live = d == path / pr["arm"]
            cols.append({"gen": "held out" if gen.startswith("holdout") else gen, "role": role + (", running now"
                         if live else ""), "head": head, "short": f"v{v}", "dir": d, "live": live, "again": again})
    for c in cols:
        c["runs"] = _runs(c["dir"])
        if c.get("live"):
            # a square for every run still to come, so the arm fills in as it goes
            want = int(pr.get("arm_runs") or pr.get("per_arm") or 0)
            tasks = list(pr.get("arm_tasks") or [])
            if not tasks and c.get("again"):
                # a re-run runs only the tasks the change moved: those its other re-run arm ran
                tasks = sorted({t for o in cols if o.get("again") and o is not c and o["gen"] == c["gen"]
                                for t in o["runs"]})
            tasks = tasks or list(pr.get("tasks") or [])
            each = max(1, want // max(1, len(tasks))) if tasks else 0
            for t in tasks:
                have = c["runs"].setdefault(t, [])
                for k in range(len(have), each):
                    have.append({"run": f"r{k + 1}", "agent": None, "passed": None, "wall_s": None, "trace": None,
                                 "pending": True})
    return cols


def _explored(v: dict) -> str:
    return "explored from the project" if v.get("project") else "explored"


def _head_w(gname: str, v: dict) -> int:
    """How wide a generation's first header line is drawn (13px, semibold), so the next one starts past it."""
    return int(len(gname) * 8.4 + (6 + len(_explored(v)) * 7.6 if v.get("explored") else 0)) + 8


def _verdicts(data: dict) -> Dict[str, dict]:
    """Per generation: what it tried (``what``), and what the counts made of it (``said``, in its colour)."""
    out = {}
    for g in data.get("lineage") or []:
        a = g.get("action") or {}
        t = a.get("test") or {}
        if not a:
            out[g["generation"]] = {"what": "", "said": "every run passed" if g.get("failed") == 0 else
                                    "nothing to change", "cls": "kept" if g.get("failed") == 0 else "soft",
                                    "explored": False, "tip": ""}
            continue
        conf = t.get("confirm") or {}
        said = t.get("verdict", "")
        if conf:
            said += " · held on re-run" if conf.get("held") else " · did not hold"
        if t.get("settled"):
            said += f" · settled after {t['settled']['after']} of {t['settled']['of']} rounds"
        rem = a.get("remedy") or {}
        out[g["generation"]] = {"what": rem.get("id") or "", "said": said,
                                "cls": "kept" if t.get("verdict") == "kept" else "reverted",
                                "explored": (a.get("because") or {}).get("source") == "exploration",
                                "project": bool(rem.get("project")),
                                "tip": f"{rem.get('value') or rem.get('id') or ''} — {t.get('why') or ''}"}
    h = data.get("holdout") or {}
    if h.get("version"):
        out["held out"] = {"what": "tasks it never evolved on", "said": h.get("says") or "",
                           "cls": "kept" if (h.get("test") or {}).get("verdict") == "kept" else "soft",
                           "explored": False, "tip": ", ".join(h.get("tasks") or [])}
    return out


def evolution_matrix(path: Path, data: dict, trace_ids: Optional[Dict[str, str]] = None,
                     progress: Optional[dict] = None) -> str:
    """The chart, with its key, or "" when no arm left records to draw. With ``progress`` (a running
    self-evolve's), the arm running now is drawn too, a square for every run still to come."""
    cols = arms_of(Path(path), data, progress)
    cols = [c for c in cols if c["runs"]]
    if not cols:
        return ""
    trace_ids = trace_ids or {}
    tasks: List[str] = []
    for c in cols:
        for t in c["runs"]:
            if t not in tasks:
                tasks.append(t)
    per = max(len(r) for c in cols for r in c["runs"].values())
    cell_w = per * (SQ + GAP) - GAP
    col_w = max(cell_w + COL_PAD + 10, 70)
    verdicts = _verdicts(data)
    pr = progress or {}
    if pr.get("status") == "running" and pr.get("generation") and pr["generation"] not in verdicts:
        # the generation running now: what it is testing, if anything yet
        testing = pr.get("testing")
        verdicts[pr["generation"]] = {"what": "testing a change" if testing else "", "said": "running",
                                      "cls": "livearm", "explored": False, "tip": testing or ""}
    gens: List[str] = []
    for c in cols:
        if c["gen"] not in gens:
            gens.append(c["gen"])
    moved = set()
    for c in cols:
        moved |= set((c.get("moved") or {}).get("up", ())) | set((c.get("moved") or {}).get("down", ()))
    # x of each column; a generation is at least wide enough for its header, and sits apart by a gutter
    xs, x, prev = [], LABEL_W, None
    for gname in gens:
        idx = [i for i, c in enumerate(cols) if c["gen"] == gname]
        if prev is not None:
            x += 22
        start = x
        for i in idx:
            xs.append(x)
            x += col_w
        x = max(x, start + GROUP_MIN, start + _head_w(gname, verdicts.get(gname) or {}))
        prev = gname
    width = x + 4
    top = HEAD
    bottom = top + len(tasks) * ROW
    height = bottom + 24
    parts: List[str] = []
    # the re-run columns sit on a band of their own
    for i, c in enumerate(cols):
        if c.get("again") and (i == 0 or not cols[i - 1].get("again")):
            j = i
            while j + 1 < len(cols) and cols[j + 1].get("again"):
                j += 1
            bx, bw = xs[i] - 5, xs[j] + col_w - COL_PAD - xs[i] + 10
            parts.append(f'<rect class="band" x="{bx}" y="{HEAD - 34}" width="{bw}" height="{bottom - HEAD + 52}" '
                         f'rx="6"><title>the tasks the change moved, run again, both arms</title></rect>')
    # generation headers: the generation, what it tried, and what the counts made of it
    for gi, gname in enumerate(gens):
        idx = [i for i, c in enumerate(cols) if c["gen"] == gname]
        gx0 = xs[idx[0]]
        v = verdicts.get(gname) or {}
        room = int(GROUP_MIN / 6.4) if len(idx) == 1 else int((xs[idx[-1]] + col_w - gx0) / 6.4)
        tip = e(v.get("tip") or "")
        parts.append(f'<text class="gh" x="{gx0}" y="13">{e(gname)}'
                     + (f'<tspan class="soft" dx="6">{_explored(v)}</tspan>' if v.get("explored") else "") + '</text>')
        if v.get("what"):
            parts.append(f'<text class="what" x="{gx0}" y="28"><title>{tip}</title>{e(_cut(v["what"], room))}</text>')
        if v.get("said"):
            parts.append(f'<text class="{v["cls"]}" x="{gx0}" y="{43 if v.get("what") else 28}"><title>{tip}</title>'
                         f'{e(_cut(v["said"], room))}</text>')
        if gi:
            sx = gx0 - 12
            parts.append(f'<line class="sep" x1="{sx}" x2="{sx}" y1="4" y2="{height - 4}"/>')
    # arm headers: what the arm runs, its harness, and the share of its runs that passed
    for c, cx in zip(cols, xs):
        runs = [r for rs in c["runs"].values() for r in rs]
        graded = [r for r in runs if r["passed"] is not None and not r.get("pending")]
        ok = sum(1 for r in graded if r["passed"])
        share = ok / len(graded) if graded else 0
        bw = col_w - COL_PAD
        tip = e(c["role"] + (" (the runs of the kept change, carried)" if c.get("carried") else ""))
        parts.append(f'<text class="arm{" livearm" if c.get("live") else ""}" x="{cx}" y="62"><title>{tip}</title>'
                     f'{"● " if c.get("live") else ""}{e(c["head"])}</text>'
                     f'<text class="soft" x="{cx}" y="74">{e(c["short"])}</text>')
        parts.append(f'<rect class="bed" x="{cx}" y="{HEAD - 6}" width="{bw}" height="3" rx="1.5"/>'
                     f'<rect class="share" x="{cx}" y="{HEAD - 6}" width="{max(0.0, bw * share):.1f}" height="3" '
                     f'rx="1.5"><title>{ok} of {len(graded)} passed</title></rect>')
        tally = f"{len(graded)} of {len(runs)} run" if c.get("live") else f"{ok}/{len(graded)}"
        parts.append(f'<text class="tally" x="{cx}" y="{bottom + 16}"><title>{ok} of {len(graded)} runs passed'
                     f'</title>{tally}</text>')
    # one row a task: its name, then a square a run in every arm that ran it
    for ri, task in enumerate(tasks):
        y = top + ri * ROW
        hot = task in moved
        cells = [f'<rect class="bg" x="0" y="{y - 3}" width="{width}" height="{ROW}" rx="3"/>']
        if hot:
            cells.append(f'<rect class="movedbar" x="0" y="{y - 1}" width="3" height="{SQ + 2}" rx="1.5">'
                         f'<title>the change moved this task</title></rect>')
        cells.append(f'<text class="tk{" hot" if hot else ""}" x="10" y="{y + SQ - 1}"><title>{e(task)}</title>'
                     f'{e(_cut(task, 29))}</text>')
        for c, cx in zip(cols, xs):
            runs = c["runs"].get(task)
            if not runs:
                continue
            for k, r in enumerate(runs):
                rx = cx + k * (SQ + GAP)
                cls = ("pending" if r.get("pending") else "pass" if r["passed"] else
                       "fail" if r["passed"] is False else "none")
                state = ("still to run" if r.get("pending") else "passed" if r["passed"] else
                         "failed" if r["passed"] is False else "not graded")
                secs = f", {r['wall_s']:.0f} s" if isinstance(r.get("wall_s"), (int, float)) else ""
                tip = f"{task} · {r['run']} · {c['gen']}, {c['role']} ({c['short']}) · {state}{secs}"
                sq = (f'<rect class="{cls}" x="{rx}" y="{y}" width="{SQ}" height="{SQ}" rx="2">'
                      f'<title>{e(tip)}</title></rect>')
                tid = trace_ids.get(str(Path(r["trace"]).resolve())) if r.get("trace") else None
                cells.append(f'<a href="/traces/{e(tid)}" aria-label="{e(tip)}">{sq}</a>' if tid else sq)
            mv = c.get("moved") or {}
            mark_x = cx + cell_w + 4
            if task in mv.get("up", ()):
                cells.append(f'<path class="up" d="M{mark_x} {y + 9} l4.5 -7 l4.5 7 z"><title>{e(task)}: passed '
                             f'more often with the change</title></path>')
            elif task in mv.get("down", ()):
                cells.append(f'<path class="down" d="M{mark_x} {y + 2} l4.5 7 l4.5 -7 z"><title>{e(task)}: passed '
                             f'less often with the change</title></path>')
        parts.append(f'<g class="row">{"".join(cells)}</g>')
    body = "".join(parts)
    # with links inside it is a group a keyboard can enter, not an image: an image's contents are not reachable
    linked = "<a " in body
    svg = (f'<svg class="evm" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
           f'role="{"group" if linked else "img"}" '
           f'aria-label="every run of every task, in every arm of every generation">{body}</svg>')
    key = ('<div class="evmkey"><span><i class="p"></i>a run that passed its check</span>'
           '<span><i class="f"></i>failed</span><span><b class="u">▲</b>the change won the task</span>'
           '<span><b class="d">▼</b>lost it</span><span>bar: share of the arm\'s runs that passed</span>'
           '<span><b style="color:var(--accent)">▍</b>a task the change moved</span>'
           '<span>shaded: the moved tasks, run again</span>'
           + ('<span>each square opens its run</span>' if linked else '') + '</div>')
    return f'<div class="evmwrap">{svg}</div>{key}'
