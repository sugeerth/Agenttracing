"""The Evolve and Evals lists, one compact card per harness or suite.

A card answers, top to bottom: did it work (a status and the pass rate from
the first generation to the last), what changed each generation (a strip of
steps: the runs that passed, the change tried and whether the counts kept
it), and what it ended with (the harness's instructions and denied tools, or
the evals still standing). The full river, every change and its evidence,
stays one click away on the run's own page.
"""

from __future__ import annotations

import html
from typing import List, Optional

__all__ = ["evolve_card", "evals_card", "start_card", "CARDS_CSS"]

CARDS_CSS = """
.ecard{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:0 0 14px}
.ecard .ehead{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px;margin-bottom:10px}
.ecard .ehead a.t{font:600 16px system-ui,sans-serif;color:var(--ink)}
.ecard .pill{font:600 12px system-ui,sans-serif;padding:2px 9px;border-radius:999px;border:1px solid var(--line);color:var(--soft)}
.ecard .pill.ok{border-color:var(--good);color:var(--good)}.ecard .pill.bad{border-color:var(--bad);color:var(--bad)}
.ecard .pill.warn{border-color:var(--warn);color:var(--ink)}
.ecard .estats{display:flex;flex-wrap:wrap;gap:6px 28px;margin:0 0 12px}
.ecard .estats div{display:flex;flex-direction:column}
.ecard .estats b{font:600 20px system-ui,sans-serif;color:var(--ink)}
.ecard .estats span{font-size:12px;color:var(--soft)}
.ecard .estats .arrow{color:var(--soft);font-weight:400;margin:0 4px}
.gstrip{display:flex;flex-wrap:wrap;gap:8px;align-items:stretch;margin:0 0 10px}
.gstep{flex:1 1 150px;max-width:230px;min-width:130px;border:1px solid var(--line);border-radius:10px;padding:8px 10px;
  background:var(--bg)}
.gstep .gn{display:flex;justify-content:space-between;font-size:12px;color:var(--soft)}
.gstep .gn b{color:var(--ink);font-weight:600}
.gstep .meter{height:6px;border-radius:3px;background:var(--line);margin:6px 0 4px;overflow:hidden;position:relative}
.gstep .meter i{display:block;height:100%;background:var(--a1);border-radius:3px}
.gstep .meter s{position:absolute;top:0;height:100%;border-right:2px solid var(--a3)}
.gstep .chg{font-size:12px;margin-top:6px;line-height:1.35}
.gstep .chg code{font-size:11px;word-break:break-all}
.gstep .ok{color:var(--good)}.gstep .bad{color:var(--bad)}.gstep .mut{color:var(--soft)}
.gstep .ev{font-size:11px;color:var(--soft);margin-top:4px}
.ends{font-size:13px;color:var(--soft);margin:2px 0 8px}
.ends li{margin:2px 0}.ends q{color:var(--ink)}
.ecard .more{font-size:13px}
.ekey{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:12px;color:var(--soft);margin:-4px 0 14px}
.ekey i{display:inline-block;width:14px;height:6px;border-radius:3px;background:var(--a1);margin-right:5px;vertical-align:1px}
.ecard .live{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;border:1px solid var(--line);
  border-left:3px solid var(--accent);border-radius:10px;padding:10px 12px;margin:0 0 12px;background:var(--bg)}
.ecard .live b{font:600 15px system-ui,sans-serif;color:var(--ink)}.ecard .live .of{font-size:12px;color:var(--soft)}
.ecard .live .what{font-size:13px;color:var(--ink);flex:1 1 260px;min-width:0}
.ecard .live .what q{color:var(--soft)}
.ecard .live .lmeter{flex:1 1 160px;max-width:320px;height:6px;border-radius:3px;background:var(--line);overflow:hidden}
.ecard .live .lmeter i{display:block;height:100%;background:var(--accent);border-radius:3px}
.ecard .live .lruns,.ecard .live .when{font-size:12px;color:var(--soft)}
.ecard .live .when{flex-basis:100%}.ecard .live .lend{flex-basis:100%;margin:0;font-size:13px}
.ecard .live .said{flex-basis:100%;font-size:12px;color:var(--soft)}.ecard .live .said ul{margin:4px 0 0;padding-left:18px}
.ecard .pill.run{border-color:var(--accent);color:var(--accent)}
.ecard .job{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;border:1px solid var(--line);
  border-left:3px solid var(--accent);border-radius:10px;padding:10px 12px;margin:8px 0 4px;background:var(--bg)}
.ecard .job b{font:600 14px system-ui,sans-serif;color:var(--ink)}
.ecard .job .when{font-size:12px;color:var(--soft)}.ecard .job .tail{flex-basis:100%;margin:0;padding-left:18px;
  font:12px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--soft)}
.ecard form.inline{display:inline;margin-left:auto}
.ecard form.go{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:end;margin:8px 0}
.ecard form.go label{display:flex;flex-direction:column;font-size:12px;color:var(--soft);gap:3px}
.ecard form.go label.wide{flex:1 1 100%}
.ecard form.go input,.ecard form.go select{font:13px system-ui,sans-serif;padding:6px 8px;border:1px solid var(--line);
  border-radius:8px;background:var(--panel);color:var(--ink);min-width:0}
.ecard form.go input{width:100%;box-sizing:border-box}
.ecard form.go button,.ecard button.stop{font:600 13px system-ui,sans-serif;padding:7px 16px;border-radius:8px;
  border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
.ecard button.stop{background:none;color:var(--bad);border-color:var(--bad)}
.ecard form.go button[disabled]{opacity:.45;cursor:not-allowed}
.ecard form.go button:focus-visible,.ecard button.stop:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
.ecard details.term,.ecard details.use{font-size:12px;color:var(--soft);margin-top:4px}
.ecard details.use{border-top:1px solid var(--line);padding-top:8px;margin-top:10px}
.ecard details.use summary{font:600 13px system-ui,sans-serif;color:var(--accent);cursor:pointer}
.ecard details.use p{font-size:13px;line-height:1.45}
.card.said pre{margin:0}
.ecard .tag{font:600 11px system-ui,sans-serif;padding:1px 7px;border-radius:999px;background:var(--chip);color:var(--soft)}
.ecard.start h2{margin:0 0 6px;font:600 16px system-ui,sans-serif;text-transform:none;letter-spacing:0;color:var(--ink)}
.ecard.start>p{font-size:14px;line-height:1.45;margin:0 0 6px}
.ways{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin:10px 0 6px}
.way{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:var(--bg);min-width:0}
.way b{display:block;font:600 14px system-ui,sans-serif;color:var(--ink);margin-bottom:4px}
.way p{font-size:13px;color:var(--soft);margin:0 0 6px;line-height:1.4}
.way pre.cmd{font-size:12px}
.ekey i.c{background:none;border-right:2px solid var(--a3);border-radius:0;width:8px;height:10px;vertical-align:-1px}
"""


def e(v) -> str:
    return html.escape(str(v), quote=True)


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "—"


def _cut(text: str, most: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= most else text[: most - 1] + "…"


def _meter(passed: int, runs: int, changed: Optional[float] = None) -> str:
    w = 100 * passed / runs if runs else 0
    mark = f'<s style="left:0;width:{100 * changed:.1f}%"></s>' if changed is not None else ""
    return f'<div class="meter" title="{passed} of {runs} passed"><i style="width:{w:.1f}%"></i>{mark}</div>'


#: what one run cost with haiku in the recorded example (12 runs, $0.51): the only cost figure the page states
HAIKU_RUN_USD = 0.043


def _opts(name: str, values, chosen) -> str:
    return (f'<select name="{name}">' + "".join(
        f'<option{" selected" if str(v) == str(chosen) else ""}>{e(v)}</option>' for v in values) + '</select>')


def _knobs() -> str:
    return (f'<label>agent {_opts("agent", ("haiku", "sonnet"), "haiku")}</label>'
            f'<label>generations {_opts("generations", range(1, 6), 3)}</label>'
            f'<label>runs a task {_opts("runs", range(1, 4), 2)}</label>')


def job_panel(job: Optional[dict], csrf: Optional[str], now: Optional[float] = None) -> str:
    """The self-evolve this hub started: running (with Stop) or how it ended, and the end of what it printed."""
    import time
    if not job:
        return ""
    now = time.time() if now is None else now
    tail = "".join(f"<li>{e(_cut(x, 220))}</li>" for x in job.get("tail") or [])
    if job["running"]:
        head = '<span class="pill run">● running</span>'
        stop = (f'<form method="post" action="/evolve/stop" class="inline"><input type="hidden" name="csrf" '
                f'value="{e(csrf)}"><button type="submit" class="stop">Stop</button></form>' if csrf else "")
    else:
        head = ('<span class="pill">■ stopped</span>' if job.get("stopped") else
                '<span class="pill ok">✓ ended</span>' if job.get("exit") == 0 else
                f'<span class="pill bad">✗ exited {e(job.get("exit"))}</span>')
        stop = ""
    return (f'<div class="job">{head}<b>{e(job["what"])}</b><span class="when">started '
            f'{_ago(now - float(job["started_at"]))} ago</span>{stop}'
            + (f'<ul class="tail">{tail}</ul>' if tail else "")
            + f'<span class="when">its output: <code>{e(job["log"])}</code></span></div>')


def start_card(*, prog: str, folder: str, claude: Optional[str], here: bool = True, csrf: Optional[str] = None,
               job: Optional[dict] = None) -> str:
    """How to evolve one of your own: the demo's six tasks, or your project when its tests fail. On a hub on this
    machine with Claude Code installed (``csrf`` given), each is a button; the commands are this machine's too:
    the program as it was started and the folder this hub lists."""
    sep = "\\" if "\\" in folder else "/"
    demo = f"{prog} self-evolve --demo"
    mine = f"cd your-project\n{prog} fix --evolve 3 -o {_quote(folder + sep + 'your-project')}"
    if not here:  # a page exported to read elsewhere: nothing about the machine it was made on
        found = 'It runs the <code>claude</code> CLI on your machine, on your Claude Code account.'
    elif claude:
        found = f'Claude Code is on this machine (<code>{e(claude)}</code>); the runs use its account.'
    else:
        found = ('There is no <code>claude</code> on PATH here: install Claude Code first, or pass '
                 '<code>--claude-bin PATH</code>.')
    busy = bool(job and job.get("running"))
    hidden = f'<input type="hidden" name="csrf" value="{e(csrf)}">' if csrf else ""
    go = '<button type="submit"' + (' disabled title="one is running"' if busy else "") + '>Start</button>'
    demo_form = (f'<form method="post" action="/evolve/start" class="go">{hidden}'
                 f'<input type="hidden" name="mode" value="demo">{_knobs()}{go}</form>' if csrf else "")
    mine_form = (f'<form method="post" action="/evolve/start" class="go">{hidden}'
                 '<input type="hidden" name="mode" value="project">'
                 '<label class="wide">project <input name="project" required placeholder="/full/path/to/your-project" '
                 'autocomplete="off" spellcheck="false"></label>'
                 '<label class="wide">its check <input name="check" placeholder="detected when empty: pytest -q, '
                 'npm test, cargo test, go test ./…" autocomplete="off" spellcheck="false"></label>'
                 f'{_knobs()}{go}</form>' if csrf else "")
    cmd_demo = f'<pre class="cmd">{e(demo)}</pre>'
    cmd_mine = f'<pre class="cmd">{e(mine)}</pre>'
    if csrf:  # the buttons lead; the commands stay one fold away, for a terminal
        cmd_demo = f'<details class="term"><summary>or in a terminal</summary>{cmd_demo}</details>'
        cmd_mine = f'<details class="term"><summary>or in a terminal</summary>{cmd_mine}</details>'
    per = 6 * 2 * 2
    return ('<div class="ecard start"><h2>Evolve your own agent</h2>'
            '<p class="muted">Your Claude Code runs the tasks under a harness that changes itself. The evals read '
            'how the runs went; when one names a change (an instruction, a denied tool), the changed harness runs the '
            'same tasks, and the change is kept only if it wins more tasks than it loses. ' + found + '</p>'
            + job_panel(job, csrf) +
            '<div class="ways">'
            '<div class="way"><b>On six small tasks</b><p>Each graded by tests the agent is never shown, so it '
            'cannot be taught to the test. A generation runs every task as many times as you say, and again when '
            f'it tests a change: up to {per} runs a generation at two a task. In the recorded example a haiku run '
            f'cost about ${HAIKU_RUN_USD:.2f}.</p>'
            f'{demo_form}{cmd_demo}</div>'
            '<div class="way"><b>On your project, when its tests fail</b><p>The same loop, on making them pass '
            'without touching the tests. It starts only when the check fails now; when it ends it names the folder '
            'to keep the change from (<code>agentdiff apply --dir …</code>).</p>'
            f'{mine_form}{cmd_mine}</div></div>'
            '<p class="muted">Every run is on <a href="/live">Live</a> while it goes, and each generation lands '
            f'here as it ends, from <code>{e(folder)}</code>.</p></div>')


def adopt_form(entry, csrf: str) -> str:
    """Use the harness this run ended with in a project's Claude Code: its instructions in CLAUDE.md, its denied
    tools in .claude/settings.local.json. Reversible: ``self-evolve --unadopt``."""
    return (f''
            f'<details class="use"><summary>Use it in your Claude Code</summary>'
            f'<p>Writes its instructions into the project\'s <code>CLAUDE.md</code>, between markers, and its denied '
            f'tools into <code>.claude/settings.local.json</code>. The paired test gave the instructions with '
            f'<code>--append-system-prompt</code>; <code>CLAUDE.md</code> gives the same words as project '
            f'instructions. <code>agentdiff self-evolve --unadopt --into PROJECT</code> takes it out again.</p>'
            f'<form method="post" action="/evolve/adopt/{e(entry.id)}" class="go"><input type="hidden" name="csrf" '
            f'value="{e(csrf)}"><label class="wide">project <input name="project" required '
            f'placeholder="/full/path/to/your-project" autocomplete="off" spellcheck="false"></label>'
            f'<button type="submit">Use it</button></form>'
            f'<pre class="cmd">agentdiff self-evolve --adopt {e(_quote(str(entry.path)))} --into your-project</pre>'
            f'</details>')


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


def live_strip(entry, now: Optional[float] = None) -> str:
    """Where a self-evolve is while it runs, or how it ended when it did not finish: the generation, what this
    arm runs (the harness as it is, or with the change being tested), its runs done, and what it last said."""
    import time
    pr = (getattr(entry, "summary", None) or {}).get("progress") or {}
    status = pr.get("status")
    if not status or status == "done":
        return ""
    now = time.time() if now is None else now
    gens = pr.get("generations") or 0
    gen = pr.get("generation") or "g0"
    try:
        n = int(str(gen).lstrip("g")) + 1
    except ValueError:
        n = 1
    arm = pr.get("arm") or ""
    testing = pr.get("testing")
    # a generation's second arm runs the change its evals named; the first, the harness as it is
    what = (f'testing a change: <q>{e(_cut(testing, 110))}</q>' if testing
            else "running the agents under the harness as it is")
    done, per = int(pr.get("runs_done") or 0), int(pr.get("per_arm") or 0)
    head = {"running": '<span class="pill run">● running</span>', "stopped": '<span class="pill">■ stopped</span>',
            "failed": '<span class="pill bad">✗ failed</span>',
            "ended": '<span class="pill warn">ended without finishing</span>'}.get(status, "")
    when = f'started {_ago(now - float(pr.get("started_at") or now))} ago'
    if status == "running":
        when += f' · last word {_ago(now - float(pr.get("updated_at") or now))} ago'
    lines = "".join(f"<li>{e(_cut(x, 200))}</li>" for x in pr.get("lines") or [])
    end = f'<p class="lend">{e(pr["end"])}</p>' if pr.get("end") and status != "running" else ""
    if status in ("stopped", "ended", "failed"):
        # the ledger holds every generation that finished: the same Start (or command) carries on from there
        end += ('<p class="lend">Started again with the same agent, it continues from the last generation that '
                'finished.</p>')
    return (f'<div class="live">{head}<b>{e(gen)}</b><span class="of">of {gens}</span>'
            + (f'<span class="what">{what}</span>' if status == "running" else "")
            + (f'<div class="lmeter" title="{done} of {per} runs of this arm done"><i style="width:'
               f'{100 * done / per if per else 0:.1f}%"></i></div><span class="lruns">{done} of {per} runs of '
               f'{e(arm or gen)}</span>' if status == "running" else "")
            + f'<span class="when">{when} · generation {n} of {gens}</span>{end}'
            + (f'<details class="said"><summary>What it said</summary><ul>{lines}</ul></details>' if lines else "")
            + '</div>')


def _tag(entry) -> str:
    """A bundled example says so, and whether it was made up or recorded (its ``EXAMPLE`` file)."""
    ex = str((getattr(entry, "summary", None) or {}).get("example") or "")
    if not ex:
        return ""
    kind = next((k for k in ("synthetic", "recorded") if ex.startswith(k)), "example")
    return f'<span class="tag" title="{e(ex)}">{kind}</span>'


def _quote(path: str) -> str:
    return f'"{path}"' if any(c in path for c in " '()&") else path


def evolve_card(entry, data: dict, *, link: bool = True, title: bool = True, adopt_csrf: Optional[str] = None) -> str:
    """One self-evolving harness: did it work, what each generation tried, what it ended with."""
    gens = data.get("lineage") or []
    href = f"/runs/{e(entry.id)}"
    stop = str(data.get("stop") or "")
    live = live_strip(entry)
    if not gens:
        return (f'<div class="ecard"><div class="ehead"><a class="t" href="{href}">{e(entry.title)}</a>{_tag(entry)}'
                + ('' if live else '<span class="pill">no generation yet</span>') + f'</div>{live}</div>')
    first, last = gens[0], gens[-1]
    p0, r0 = first["runs"] - first["failed"], first["runs"]
    p1, r1 = last["runs"] - last["failed"], last["runs"]
    kept = sum(1 for g in gens if ((g.get("action") or {}).get("test") or {}).get("verdict") == "kept")
    reverted = sum(1 for g in gens if ((g.get("action") or {}).get("test") or {}).get("verdict") == "reverted")
    born = sum(len((g.get("evals") or {}).get("born") or []) for g in gens)
    retired = sum(len((g.get("evals") or {}).get("retired") or []) for g in gens)
    if last["failed"] == 0:
        pill = '<span class="pill ok">✓ every run passes</span>'
    elif "no eval" in stop or "nothing" in stop:
        pill = '<span class="pill warn">stopped: no change it can make answers the failures</span>'
    else:
        pill = f'<span class="pill bad">✗ {last["failed"]} of {r1} still fail</span>'
    moved = (f'<b>{_pct(p0, r0)}<span class="arrow">→</span>{_pct(p1, r1)}</b>'
             f'<span>runs that passed, g0 → {e(last["generation"])} ({p0}/{r0} → {p1}/{r1})</span>' if len(gens) > 1 else
             f'<b>{_pct(p1, r1)}</b><span>runs that passed ({p1}/{r1})</span>')
    stats = (f'<div class="estats"><div>{moved}</div>'
             f'<div><b>{len(gens)}</b><span>generation{"s" if len(gens) != 1 else ""}</span></div>'
             f'<div><b>{kept}</b><span>change{"s" if kept != 1 else ""} kept'
             + (f', {reverted} reverted' if reverted else "") + '</span></div>'
             f'<div><b>+{born} −{retired}</b><span>evals born, retired</span></div></div>')
    steps: List[str] = []
    for g in gens:
        a = g.get("action") or {}
        t = a.get("test") or {}
        rem = (a.get("remedy") or {}).get("id")
        changed = (t.get("passed") or {}).get("changed")
        ch = changed[0] / changed[1] if changed and changed[1] else None
        if g["failed"] == 0:
            chg = '<span class="ok">✓ every run passed</span>'
        elif not rem:
            chg = '<span class="mut">nothing the harness can change answers these failures</span>'
        elif t.get("verdict") == "kept":
            chg = (f'<span class="ok">✓ kept</span> <code>{e(_cut(rem, 48))}</code><br><span class="mut">'
                   f'{changed[0]}/{changed[1]} passed with it</span>' if changed else
                   f'<span class="ok">✓ kept</span> <code>{e(_cut(rem, 48))}</code>')
        elif t.get("verdict") == "reverted":
            chg = (f'<span class="bad">✗ reverted</span> <code>{e(_cut(rem, 48))}</code><br><span class="mut">'
                   f'{changed[0]}/{changed[1]} with it: no better</span>' if changed else
                   f'<span class="bad">✗ reverted</span> <code>{e(_cut(rem, 48))}</code>')
        else:
            chg = f'<span class="mut">tried</span> <code>{e(_cut(rem, 48))}</code>'
        ev = g.get("evals") or {}
        evs = []
        if ev.get("born"):
            evs.append("+" + ", ".join(ev["born"]))
        if ev.get("retired"):
            evs.append("−" + ", ".join(ev["retired"]))
        steps.append(f'<div class="gstep"><div class="gn"><b>{e(g["generation"])}</b>'
                     f'<span>v{e((g.get("harness") or {}).get("version", "?"))} · '
                     f'{g["runs"] - g["failed"]}/{g["runs"]}</span></div>'
                     f'{_meter(g["runs"] - g["failed"], g["runs"], ch)}<div class="chg">{chg}</div>'
                     + (f'<div class="ev" title="evals born and retired">evals {e(_cut(" ".join(evs), 60))}</div>'
                        if evs else "") + '</div>')
    harness = data.get("harness") or {}
    ends = []
    for ins in harness.get("instructions") or []:
        ends.append(f'<li>told: <q>{e(_cut(ins, 140))}</q></li>')
    if harness.get("deny_tools"):
        ends.append(f'<li>denied: {e(", ".join(harness["deny_tools"]))}</li>')
    if harness.get("max_turns"):
        ends.append(f'<li>at most {e(harness["max_turns"])} turns</li>')
    ends_html = (f'<div class="ends">It ends with v{e(harness.get("version", 0))}:<ul>{"".join(ends)}</ul></div>'
                 if ends else '<div class="ends">It ends as the agent ships: nothing added.</div>')
    name = (f'<a class="t" href="{href}">{e(entry.title)}</a>' if title else "") + _tag(entry)
    if live:
        pill = ""  # still going: the strip says where it is, not how it ended
    use = adopt_form(entry, adopt_csrf) if adopt_csrf and harness.get("version") and not live else ""
    return (f'<div class="ecard"><div class="ehead">{name}{pill}</div>{live}'
            f'{stats}<div class="gstrip">{"".join(steps)}</div>{ends_html}{use}'
            + (f'<a class="more" href="{href}">Every change and its evidence →</a>' if link else "") + '</div>')


def evals_card(entry, data: dict, *, link: bool = True, title: bool = True) -> str:
    """One eval suite: what it caught of failures it had never seen, generation by generation, and each eval's life."""
    lineage = [g for g in data.get("lineage") or [] if not g.get("skipped")]
    evals = data.get("evals") or []
    href = f"/runs/{e(entry.id)}"
    active = [x for x in evals if x.get("status") == "active"]
    gone = [x for x in evals if x.get("status") == "retired"]
    covs = [g["forward"]["coverage"] for g in lineage if g.get("arrived") and isinstance((g.get("forward") or {}).get("coverage"), (int, float))]
    best = max(covs) if covs else None
    pill = (f'<span class="pill ok">{len(active)} eval{"s" if len(active) != 1 else ""} standing</span>' if active
            else '<span class="pill">no eval standing</span>')
    stats = (f'<div class="estats"><div><b>{"—" if best is None else f"{best:.0%}"}</b>'
             f'<span title="what the suite carried in caught of failures it had never seen">best forward coverage</span></div>'
             f'<div><b>{len(lineage)}</b><span>generation{"s" if len(lineage) != 1 else ""}</span></div>'
             f'<div><b>+{len(evals)} −{len(gone)}</b><span>evals born, retired</span></div></div>')
    steps = []
    for g in lineage:
        f = g.get("forward") or {}
        arrived = g.get("arrived")
        body = (f'{_meter(f.get("caught", 0), g.get("wrong") or 0)}<div class="chg">'
                + ('<span class="ok">nothing wrong to catch</span>' if not g.get("wrong") else
                   f'caught <b>{f.get("caught", 0)}</b> of {g.get("wrong", 0)} it had never seen'
                   + (f', <span class="bad">{f.get("false_alarms")} false alarm(s)</span>' if f.get("false_alarms") else "")
                   if arrived else f'<span class="mut">no eval carried in yet; {g.get("wrong", 0)} to catch</span>')
                + '</div>')
        evs = []
        if g.get("born"):
            evs.append("+" + ", ".join(g["born"]))
        if g.get("retired"):
            evs.append("−" + ", ".join(g["retired"]))
        steps.append(f'<div class="gstep"><div class="gn"><b>{e(g["generation"])}</b><span>{g.get("wrong", 0)} of '
                     f'{g.get("runs", 0)} wrong</span></div>{body}'
                     + (f'<div class="ev">evals {e(_cut(" ".join(evs), 60))}</div>' if evs else "") + '</div>')
    lives = "".join(f'<li><code>{e(x.get("id"))}</code>: {e(_cut(x.get("says") or "", 90))} · born {e(x.get("born"))}'
                    + (f', retired {e(x.get("retired_at"))}' if x.get("retired_at") else ", standing") + '</li>'
                    for x in evals[:6])
    name = (f'<a class="t" href="{href}">{e(entry.title)}</a>' if title else "") + _tag(entry)
    return (f'<div class="ecard"><div class="ehead">{name}{pill}'
            f'<span class="mut" style="font-size:13px;color:var(--soft)">target: {e(data.get("says") or "runs that failed")}</span></div>'
            f'{stats}<div class="gstrip">{"".join(steps)}</div>'
            + (f'<div class="ends">Its evals:<ul>{lives}</ul></div>' if lives else "")
            + (f'<a class="more" href="{href}">Every eval and why it entered and left →</a>' if link else "") + '</div>')
