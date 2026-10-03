"""The ``timeline`` command: runs on their clocks, and where to look first.

    agentdiff timeline run.json                one run: its threads, laps and folds, and where to look
    agentdiff timeline a.json b.json           two runs on one axis, and the first step they differ
    agentdiff timeline traces/ [more/ ...]     every run, one row each on one clock, and each one's place to look
    agentdiff timeline run.json --long         a run of hours or days: its sessions, bursts, loops over hours,
                                               the stall, phase by phase (automatic for a run that long)

It prints one line per run that says where to look first, and writes the
drawings to one page (``timeline.html`` unless ``-o``), the same server-
side SVG the hub draws, with no script. The hub draws the same views live
(``/traces/<id>``, ``/timeline``). See :mod:`agentdiff.timeline`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["register", "run"]


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "timeline", help="runs on their clocks: one run (threads, laps, folded quiet stretches), two on one axis, or "
                         "a directory as one row per run; prints where to look first and writes the page")
    p.add_argument("paths", nargs="+", metavar="TRACE_OR_DIR", help="one trace, two traces, or directories of traces")
    p.add_argument("-o", "--output", default="timeline.html", metavar="FILE", help="the page (default timeline.html)")
    p.add_argument("--axis", choices=("step", "time"), default="step", help="two runs: align by step or by time")
    p.add_argument("--scale", choices=("shared", "own"), default="shared",
                   help="directories: one clock for every run, or each its own")
    p.add_argument("--json", action="store_true", help="print the reading as JSON instead of lines")
    p.add_argument("--long", action="store_true",
                   help="read it as a long run (sessions, bursts, loops over hours); automatic past an hour or "
                        "600 steps")
    p.set_defaults(func=run)


def _load(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("steps"), list) else None


def _traces(d: Path) -> list:
    out = []
    for p in sorted(d.glob("*.json")):
        if p.name.endswith((".meta.json",)) or p.name in ("aggregate.json", "RUN_MANIFEST.json", "plan.json", "duel.json"):
            continue
        if p.name.endswith(".live.json") and p.with_name(p.name[: -len(".live.json")] + ".json").exists():
            continue
        data = _load(p)
        if data is not None:
            out.append((p, data))
    return out


def _mark(success, live=False) -> str:
    return "●" if live else "✓" if success is True else "✗" if success is False else "–"


def run(args: argparse.Namespace) -> int:
    from ..hub import views, viz
    from ..timeline import compare, ribbons, timeline
    paths = [Path(p) for p in args.paths]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        print(f"error: not found: {', '.join(missing)}", file=sys.stderr)
        return 2
    body, reading = [], {}
    if all(p.is_file() for p in paths) and len(paths) in (1, 2):
        datas = [_load(p) for p in paths]
        bad = [str(p) for p, d in zip(paths, datas) if d is None]
        if bad:
            print(f"error: not a trace: {', '.join(bad)}", file=sys.stderr)
            return 2
        tls = [timeline(d) for d in datas]
        from ..longrun import is_long
        if args.long or any(is_long(d) for d in datas):
            return _long(args, paths, datas, tls)
        for p, t in zip(paths, tls):
            here = t["look_here"] or {}
            print(f"{_mark(t['success'], t['in_progress'])} {p.name}: {len(t['steps'])} steps, {len(t['lanes'])} "
                  f"thread(s), {len(t['laps'])} lap(s), {t['span_s']:.1f}s ({t['basis']} clock)")
            if here:
                print(f"  look here: {here['sentence']}")
            body.append(f'<h2>{views.e(p.name)}</h2><div class="card">'
                        f'<p class="note">{views.e(here.get("sentence"))}</p>{viz.trunk_svg([t], link=False)}'
                        f'{viz.run_timeline(t, link=False)}</div>')
        reading = {"timelines": tls}
        if len(datas) == 2:
            c = compare(datas[0], datas[1])
            print(f"  {c['sentence']}")
            body.insert(0, f'<h2>Two runs on one axis</h2><div class="card"><p class="note">{views.e(c["sentence"])}</p>'
                           f'{viz.trunk_svg([c["a"], c["b"]], cmp=c, axis=args.axis, link=False)}'
                           f'{viz.pair_timeline(c, axis=args.axis)}</div>')
            reading = {"compare": c}
        title = paths[0].stem if len(paths) == 1 else f"{paths[0].stem} vs {paths[1].stem}"
    else:
        groups = []
        for p in paths:
            found = _traces(p) if p.is_dir() else ([(p, _load(p))] if _load(p) else [])
            if not found:
                print(f"error: no traces in {p}", file=sys.stderr)
                return 2
            rows = ribbons([d for _, d in found])
            labels = [f.stem for f, _ in found]
            groups.append((str(p), rows, labels))
            failed = [(lab, r) for lab, r in zip(labels, rows) if r["success"] is False]
            print(f"{p}: {len(rows)} run(s), {len(failed)} failed")
            for lab, r in zip(labels, rows):
                here = r.get("look_here") or {}
                if r["success"] is False or r["in_progress"]:
                    print(f"  {_mark(r['success'], r['in_progress'])} {lab}: {here.get('sentence') or 'no step to point at'}")
            body.append(views.ribbon_group(str(p), rows, [None] * len(rows), labels, args.scale))
        reading = {"groups": [{"path": g, "runs": rows} for g, rows, _ in groups]}
        title = "Every run on one clock"
    if args.json:
        print(json.dumps(reading, indent=1, default=str))
    page = views.layout(title, f"<h1>{views.e(title)}</h1>" + "".join(body), brand="AgentDiff")
    Path(args.output).write_text(page, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


def _long(args, paths: list, datas: list, tls: list) -> int:
    """One or two long runs: the story phase by phase in the terminal, the long views on the page."""
    from ..hub import longviz, views
    from ..longrun import dur, longrun, when
    readings = [longrun(d) for d in datas]
    body = []
    for p, r, t in zip(paths, readings, tls):
        sa, span = r.get("started_at"), r["span_s"]
        print(f"{_mark(r['success'], r['in_progress'])} {p.name}: {r['sentence']}")
        if r.get("look_here"):
            print(f"  look here: {r['look_here']['sentence']}")
        for ph in longviz.phases(r):
            if ph["kind"] == "idle":
                print(f"    — {dur(ph['seconds'])} idle —")
                continue
            a, b = ph["bursts"]
            name = f"burst {a}" if a == b else f"bursts {a}-{b}"
            mine = r["bursts"][a - 1:b]
            if ph["kind"] == "loop":
                what = f"↻ loop: the same calls {ph['count']}x" + (f", {ph['failing']} failing" if ph["failing"] else "")
            elif ph["kind"] == "filler":
                what = "filler: reading and editing, no check changed"
            else:
                what = next((x["note"] for x in reversed(mine) if x["note"]), ph.get("status") or "")
            print(f"    {when(ph['from'], sa, span):>12}  {name:<14} {dur(ph['to'] - ph['from']):>8}  "
                  f"{ph['calls']:>6,} calls  {what}")
        body.append(f'<h2>{views.e(p.name)}</h2><div class="card"><p class="note">{views.e(r["sentence"])}</p>'
                    f'{longviz.long_overview(r, t["steps"])}{longviz.chapters_table(r)}'
                    f'{longviz.rhythm_grid(r)}{longviz.pace_chart(r)}{longviz.phase_treemap(r, t["steps"])}</div>')
    if len(datas) == 2:
        names = tuple(str(r.get("agent") or p.stem) for r, p in zip(readings, paths))
        body.insert(0, '<h2>Two long runs, checkpoint by checkpoint</h2><div class="card">'
                       + longviz.diff_timeline(datas[0], datas[1], tls[0]["steps"], tls[1]["steps"], names=names)
                       + "</div>")
    if args.json:
        print(json.dumps({"long": readings}, indent=1, default=str))
    title = paths[0].stem if len(paths) == 1 else f"{paths[0].stem} vs {paths[1].stem}"
    page = views.layout(title, f"<h1>{views.e(title)}</h1>" + "".join(body), brand="AgentDiff")
    Path(args.output).write_text(page, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0
