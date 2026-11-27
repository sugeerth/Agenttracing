"""The ten findings of ``docs/FINDINGS.md``, checked against your own runs (``agentdiff findings RUNS/``).

Each finding becomes a question your runs can answer: do agents here finish without checking, and do those
runs fail more; were the changes you kept re-run on fresh runs; how much does a clean streak here still allow;
would more runs or more tasks narrow your pass rate; and so on. Each answer is one of:

- ``applies``: the finding shows in these runs, with the numbers that show it and what to do;
- ``clear``: it was checked here and does not show;
- ``cannot tell``: these runs cannot answer it, and the reason says what would.

Every number is a count or an exact test over the runs given, from the same functions the rest of AgentDiff
uses (the guard replay, the reliability reading, the cost ratio, the from-git leak check); nothing is judged by
a model. Standard library only.
"""

from __future__ import annotations

import json
from math import comb
from pathlib import Path
from typing import Dict, List, Optional, Sequence

__all__ = ["FINDINGS", "check_findings", "load", "evolution_records", "fisher_one_sided", "arms",
           "findings_html"]

#: (number, the finding, what it advises), as docs/FINDINGS.md states them
FINDINGS = (
    (1, "Checking after the last edit goes with passing",
     "hold agents to the project's own check at the finish (agentdiff guard --install)"),
    (2, "The change you keep looks better than it is",
     "re-run a change on fresh runs before keeping it, and report the re-run's gain"),
    (3, "Testing one change at a time keeps placebos",
     "test three to five candidates together and keep a winner only when it replicates"),
    (4, "A clean streak proves less than it looks",
     "report pass rates with exact intervals, and read a short unbroken record as not yet known"),
    (5, "When a task's runs agree, add tasks, not runs",
     "spend the next runs where they narrow the pass rate most"),
    (6, "Repeated runs part early, then go back to the same work",
     "compare runs by the actions they take; an early fork is not a different strategy"),
    (7, "Telling two agents apart needs runs a side",
     "run at least four a side on the tasks you compare, and test against the runs relabelled at random"),
    (8, "A run can read the fix from a git repository above it",
     "stop git searching above each run's folder (GIT_CEILING_DIRECTORIES)"),
    (9, "The changes that help usually cost more",
     "report each change's cost paired by task, beside its gain"),
    (10, "Tests that grade agents let wrong code through",
     "check how many small wrong versions of the fix the grader rejects (--grader-mutants N)"),
)

APPLIES, CLEAR, CANNOT = "applies", "clear", "cannot tell"


def fisher_one_sided(k1: int, n1: int, k2: int, n2: int) -> Optional[float]:
    """The one-sided Fisher exact p that the first group fails more than the second: the chance, with the
    margins fixed, of ``k1`` or more failures among the first group's ``n1`` runs."""
    n, k = n1 + n2, k1 + k2
    if not n1 or not n2 or not k:
        return None
    total = comb(n, k)
    return sum(comb(n1, x) * comb(n2, k - x) for x in range(k1, min(n1, k) + 1)) / total


def load(paths: Sequence) -> tuple:
    """Every trace under ``paths`` (files or folders, searched all the way down), the raw records, and the
    files that were not traces. Returns ``(trajectories, records, skipped)``."""
    from .trace import Trajectory
    files: List[Path] = []
    for given in paths:
        p = Path(given)
        files += sorted(p.rglob("*.json")) if p.is_dir() else [p]
    runs, records, skipped = [], [], 0
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            runs.append(Trajectory.from_json(data))
        except Exception:  # noqa: BLE001 — a report, a config, a record that is not a trace
            skipped += 1
            continue
        records.append({"path": str(f), "data": data})
    return runs, records, skipped


def evolution_records(paths: Sequence) -> List[dict]:
    """Every ``self-evolve.json`` under ``paths``: ``[{"path", "data"}]``."""
    out = []
    for given in paths:
        p = Path(given)
        for f in (sorted(p.rglob("self-evolve.json")) if p.is_dir() else []):
            try:
                out.append({"path": str(f), "data": json.loads(f.read_text(encoding="utf-8"))})
            except (OSError, ValueError):
                continue
    return out


def arms(runs: Sequence, records: Sequence[dict]) -> Dict[str, list]:
    """The runs by arm: the agent's name, with its folder beside it when the same name ran from more than one
    folder, so two harness versions that share an agent's name are never merged. The folder is named from where
    that agent's folders part, without a closing ``traces``."""
    import os
    folders: Dict[str, set] = {}
    for t, r in zip(runs, records):
        folders.setdefault(t.agent.name, set()).add(str(Path(r["path"]).parent))
    common = {name: os.path.commonpath(sorted(f)) for name, f in folders.items() if len(f) > 1}
    out: Dict[str, list] = {}
    for i, (t, r) in enumerate(zip(runs, records)):
        name = t.agent.name
        if name in common:
            rel = Path(os.path.relpath(Path(r["path"]).parent, common[name]))
            if rel.name == "traces" and str(rel.parent) != ".":
                rel = rel.parent
            name = f"{name} ({rel.as_posix()})"
        out.setdefault(name, []).append(i)
    return out


def _row(no: int, status: str, figure: str, yours: str, **extra) -> dict:
    _, title, advice = FINDINGS[no - 1]
    return {"no": no, "title": title, "status": status, "figure": figure, "yours": yours, "do": advice, **extra}


def _f1(runs, ungraded) -> dict:
    from .guard import replay
    rep = replay(runs, enabled=("check",), ungraded=ungraded)
    edited = [r for r in rep["per_run"] if r["edited"] and r["success"] is not None]
    if not edited:
        return _row(1, CANNOT, "–", "No graded run here edited a file, so there is no last edit to check after.")
    un = [r for r in edited if (r["refused"].get("check") or {}).get("why") == "unchecked"]
    ck = [r for r in edited if r not in un]
    k1, k2 = sum(1 for r in un if r["success"] is False), sum(1 for r in ck if r["success"] is False)
    p = fisher_one_sided(k1, len(un), k2, len(ck))
    g = rep["guards"]["check"]
    guard = (f" Replayed over these runs, the check guard steps into {g['in_failed']} of {g['failed']} failing "
             f"runs and sends {g['in_passed']} of {g['passed']} passing runs back once.")
    if not un:
        return _row(1, CLEAR, f"0 of {len(edited)}", f"Every one of the {len(edited)} graded runs that edited a "
                    f"file ran a check after its last edit." + guard, counts=[k1, 0, k2, len(ck)])
    if not ck:
        return _row(1, APPLIES, f"{len(un)} of {len(edited)}", f"None of the {len(edited)} graded runs that edited "
                    f"a file ran a check after its last edit; {k1} of them failed. There are no checked runs here "
                    f"to compare with." + guard, counts=[k1, len(un), 0, 0])
    rate = f"{k1} of {len(un)} unchecked finishes failed, against {k2} of {len(ck)} checked ones"
    ps = f" (one-sided Fisher exact p = {p:.3g})" if p is not None else ""
    worse = len(un) and len(ck) and k1 / len(un) > k2 / len(ck)
    return _row(1, APPLIES if worse else CLEAR, f"{k1} of {len(un)}" if worse else f"{len(un)} unchecked",
                rate + ps + "." + guard, counts=[k1, len(un), k2, len(ck)], p=p)


def _kept(evolutions) -> List[dict]:
    kept = []
    for rec in evolutions:
        for g in rec["data"].get("lineage") or []:
            test = ((g.get("action") or {}).get("test")) or {}
            if test.get("verdict") == "kept":
                kept.append({"record": rec["path"], "generation": g.get("generation"), "test": test,
                             "remedy": ((g.get("action") or {}).get("remedy") or {}).get("id")})
    return kept


def _f2(evolutions) -> dict:
    kept = _kept(evolutions)
    if not kept:
        return _row(2, CANNOT, "–", "No self-evolve record here kept a change (none found, or none kept)."
                    if evolutions else "No self-evolve record here: point this at a self-evolve output folder "
                    "to check its kept changes.")
    bare = [k for k in kept if not k["test"].get("confirm")]
    if not bare:
        return _row(2, CLEAR, f"0 of {len(kept)}", f"All {len(kept)} kept change(s) were re-run on fresh runs "
                    f"before they stayed.", kept=len(kept))
    names = ", ".join(f"{k['remedy']} ({k['generation']})" for k in bare[:4])
    return _row(2, APPLIES, f"{len(bare)} of {len(kept)}", f"{len(bare)} of {len(kept)} kept change(s) stand on "
                f"the test that picked them, never re-run on fresh runs: {names}. Their gain is likely overstated.",
                kept=len(kept), unconfirmed=len(bare))


def _f3(evolutions) -> dict:
    kept = _kept(evolutions)
    if not kept:
        return _row(3, CANNOT, "–", "No kept change to read here.")
    weak = [k for k in kept if not isinstance(k["test"].get("p_value"), (int, float)) or k["test"]["p_value"] > 0.05]
    if not weak:
        return _row(3, CLEAR, f"0 of {len(kept)}", f"Each of the {len(kept)} kept change(s) beat chance in its "
                    f"own test (p ≤ 0.05).", kept=len(kept))
    def say(k):
        p = k["test"].get("p_value")
        return f"{k['remedy']} ({k['generation']}, p {p:.2g})" if isinstance(p, (int, float)) else \
            f"{k['remedy']} ({k['generation']}, no test p)"
    return _row(3, APPLIES, f"{len(weak)} of {len(kept)}", f"{len(weak)} of {len(kept)} kept change(s) were kept "
                f"on a test a change that did nothing could pass: " + ", ".join(say(k) for k in weak[:4]) + ".",
                kept=len(kept), weak=len(weak))


def _rel(runs, groups):
    from .reliability import reliability
    by: dict = {}
    for arm, idx in groups.items():
        for i in idx:
            by.setdefault(runs[i].task.id, {}).setdefault(arm, []).append(runs[i])
    try:
        return reliability(by)
    except ValueError:
        return None


def _f4(rel) -> dict:
    if not rel:
        return _row(4, CANNOT, "–", "No runs to read.")
    lines, open_tasks, worst = [], 0, None
    for arm, a in rel["per_agent"].items():
        streaks = [t for t in a.get("per_task") or [] if t.get("unbroken")]
        for t in streaks:
            bound = t["unbroken"].get("other_rate_at_most")
            if isinstance(bound, (int, float)) and (worst is None or bound > worst[0]):
                worst = (bound, arm, t["task"], t["runs"], t["unbroken"]["record"])
        plan = a.get("run_plan") or {}
        open_tasks += plan.get("open", 0)
        if plan.get("open"):
            lines.append(f"{arm}: {plan['open']} of {plan['tasks']} task(s) still open, "
                         f"{plan.get('more_runs', 0)} more run(s) to rule out a {plan.get('target', 0.1):.0%} rate")
    if worst is None:
        return _row(4, CLEAR, "0", "No task here has an unbroken record: every task has both passed and failed, "
                    "so its rate is already known to be neither 0 nor 1.")
    bound, arm, task, n, record = worst
    return _row(4, APPLIES, f"{bound:.0%}", f"The weakest streak: {arm} on {task} {record} ({n} run(s)), which "
                f"still allows a {bound:.0%} rate the other way (95%, exact). " + "; ".join(lines[:4]) + ".",
                open=open_tasks)


def _f5(rel) -> dict:
    if not rel:
        return _row(5, CANNOT, "–", "No runs to read.")
    says, shares = [], []
    for arm, a in rel["per_agent"].items():
        rt = a.get("runs_or_tasks") or {}
        share = rt.get("runs_share_of_tasks_gain")
        if isinstance(share, (int, float)):
            shares.append(share)
            says.append(f"{arm}: more runs buy {share:.0%} of what as many new tasks would")
    if not shares:
        return _row(5, CANNOT, "–", "Too few tasks or runs a task to estimate how alike a task's runs are.")
    low = min(shares)
    return _row(5, APPLIES if low < 0.5 else CLEAR, f"{low:.0%}", "; ".join(says[:4]) + ". "
                + ("Where it is low, the next runs are better spent on new tasks." if low < 0.5 else
                   "More runs of these tasks still narrow the pass rate well."))


def _f6(rel) -> dict:
    if not rel:
        return _row(6, CANNOT, "–", "No runs to read.")
    says, at = [], None
    for arm, a in rel["per_agent"].items():
        part = (a.get("trajectory_consistency") or {}).get("parting") or {}
        if part.get("says"):
            says.append(f"{arm}: {part['says']}")
            at = part.get("at") if at is None else at
    if not says:
        return _row(6, CANNOT, "–", "No task here has two passing runs that take different paths.")
    return _row(6, APPLIES, f"step {at:g}" if isinstance(at, (int, float)) else "–", "; ".join(says[:4]) + ".")


def _f7(runs, groups) -> dict:
    if len(groups) < 2:
        return _row(7, CANNOT, "–", "Only one arm here: there is nothing to compare it with.")
    per: dict = {}
    for arm, idx in groups.items():
        for i in idx:
            per.setdefault(runs[i].task.id, {}).setdefault(arm, 0)
            per[runs[i].task.id][arm] += 1
    shared = {t: c for t, c in per.items() if len(c) >= 2}
    if not shared:
        return _row(7, CANNOT, "–", "No task here was run by two arms.")
    thin = [t for t, c in shared.items() if min(c.values()) < 4]
    if not thin:
        return _row(7, CLEAR, f"0 of {len(shared)}", f"Every one of the {len(shared)} task(s) two arms share has at "
                    f"least 4 runs a side.")
    return _row(7, APPLIES, f"{len(thin)} of {len(shared)}", f"{len(thin)} of the {len(shared)} task(s) two arms "
                f"share have fewer than 4 runs on some side. There, no difference in how the arms' runs go can be "
                f"shown beyond chance, however large it looks.", thin=len(thin), shared=len(shared))


def _f8(runs) -> dict:
    from .gittasks import reached_outside
    ran_git, found = 0, []
    for t in runs:
        if any(s.type == "tool_call" and s.name == "Bash" and "git " in str(s.input) for s in t.steps):
            ran_git += 1
        hits = [h for h in reached_outside(t, []) if h["kind"] == "found a git repository outside the workspace"]
        if hits:
            found.append((t.trace_id, hits[0]["evidence"]))
    if not ran_git:
        return _row(8, CANNOT, "–", "No run here ran git, so none could have found a repository.")
    if not found:
        return _row(8, CLEAR, f"0 of {ran_git}", f"{ran_git} run(s) ran git, and none found a repository.")
    ex = ", ".join(f"{tid} (`{ev[:40]}`)" for tid, ev in found[:3])
    return _row(8, APPLIES, f"{len(found)} of {ran_git}", f"{len(found)} of the {ran_git} run(s) that ran git found "
                f"a repository: {ex}. If these tasks were built from a repository's history, the fix may have been "
                f"in reach; if the workspace is meant to be a repository, this is expected.", found=len(found))


def _f9(runs, groups) -> dict:
    from .efficiency import resource_ratio_over_runs
    names = sorted(groups)
    best = None
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            by: dict = {}
            for side, arm in (("a", a), ("b", b)):
                for j in groups[arm]:
                    by.setdefault(runs[j].task.id, {}).setdefault(side, []).append(runs[j])
            by = {t: s for t, s in by.items() if len(s) == 2}
            if not by:
                continue
            r = resource_ratio_over_runs(by, "tokens")
            if r.get("ratio") is not None and (best is None or r["tasks"] > best[2]["tasks"]):
                best = (a, b, r)
    if best is None:
        return _row(9, CANNOT, "–", "No two arms here share tasks with tokens logged on both sides.")
    a, b, r = best
    sig = r.get("p") is not None and r["p"] <= 0.05
    return _row(9, APPLIES if r["ratio"] > 1 else CLEAR, f"×{r['ratio']:.2f}", f"{b} uses ×{r['ratio']:.2f} the "
                f"tokens a run of {a}, paired over {r['tasks']} task(s)"
                + (f" (95% {r['low']:.2f}–{r['high']:.2f}" if r.get("low") is not None else " (")
                + (f", p {r['p']:.2g}" if r.get("p") is not None else "") + ")"
                + (", shown by the tasks." if sig else ", not shown by the tasks."), ratio=r["ratio"])


def _f10() -> dict:
    return _row(10, CANNOT, "–", "Traces do not carry the grader. Tasks built from a repository's history can be "
                "checked: agentdiff self-evolve --from-git REPO --grader-mutants 12.")


def check_findings(runs: Sequence, records: Sequence[dict], evolutions: Sequence[dict] = (),
                   label: str = "") -> dict:
    """Each finding, checked against ``runs`` (with their raw ``records``, for folders and grading) and any
    self-evolve ``evolutions``. Returns ``{"runs", "failed", "passed", "ungraded", "arms", "findings", "says"}``."""
    from .guard import is_ungraded
    runs = list(runs)
    ungraded = {i for i, r in enumerate(records) if is_ungraded(r["data"])}
    groups = arms(runs, records)
    rel = _rel(runs, groups) if runs else None
    rows = [_f1(runs, ungraded), _f2(evolutions), _f3(evolutions), _f4(rel), _f5(rel), _f6(rel),
            _f7(runs, groups), _f8(runs), _f9(runs, groups), _f10()]
    graded = [t for i, t in enumerate(runs) if i not in ungraded]
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in (APPLIES, CLEAR, CANNOT)}
    return {"label": label, "runs": len(runs), "failed": sum(1 for t in graded if not t.outcome.success),
            "passed": sum(1 for t in graded if t.outcome.success), "ungraded": len(ungraded),
            "arms": {k: len(v) for k, v in groups.items()}, "evolutions": len(evolutions), "findings": rows,
            "counts": counts,
            "says": f"{counts[APPLIES]} of the 10 findings apply to these runs, {counts[CLEAR]} checked clear, "
                    f"{counts[CANNOT]} cannot be told from them"}


_CSS = """
:root{--bg:#fff;--ink:#111;--ink2:#5c5c5c;--rule:#d8d8d8;--soft:#f3f3f3;--red:#e4002b;--grey:#9a9a9a}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0e0e0e;--ink:#f2f2f2;--ink2:#a9a9a9;
--rule:#2e2e2e;--soft:#1a1a1a;--red:#ff4060;--grey:#6d6d6d}}
:root[data-theme="dark"]{--bg:#0e0e0e;--ink:#f2f2f2;--ink2:#a9a9a9;--rule:#2e2e2e;--soft:#1a1a1a;--red:#ff4060;
--grey:#6d6d6d}
*{box-sizing:border-box;min-width:0}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 Archivo,system-ui,sans-serif;overflow-x:clip}
.band{padding-block:clamp(24px,4vw,52px);padding-left:max(5vw,16px,env(safe-area-inset-left,0px));
padding-right:max(5vw,16px,env(safe-area-inset-right,0px))}
.wrap{max-width:1180px;margin:0 auto}
.top{border-top:6px solid var(--ink)}
h1{font-size:clamp(30px,4.4vw,50px);line-height:1.05;letter-spacing:-.02em;font-weight:800;margin:0 0 12px}
h2{font-size:clamp(20px,2.4vw,26px);line-height:1.15;margin:0 0 10px;font-weight:800}
.lede{color:var(--ink2);margin:0 0 24px;max-width:66ch;font-size:17px}
.board{display:grid;grid-template-columns:repeat(10,minmax(0,1fr));gap:6px;margin-bottom:10px}
.tile{display:flex;flex-direction:column;gap:8px;text-decoration:none;color:inherit;border:2px solid var(--ink);
border-radius:4px;padding:10px 8px;min-height:112px}
.tile .n{font-size:13px;font-weight:800}
.tile .v{display:block;font-size:clamp(14px,1.9vw,25px);font-weight:800;line-height:1.05;letter-spacing:-.02em;
white-space:nowrap}
.tile .s{margin-top:auto;font-size:12px;font-weight:700}
.tile.applies{background:var(--red);border-color:var(--red);color:#fff}
.tile.clear{background:var(--bg)}
.tile.cannot{border-style:dashed;border-color:var(--grey);color:var(--ink2)}
.key{display:flex;flex-wrap:wrap;gap:6px 20px;font-size:14px;color:var(--ink2);margin-bottom:6px}
.key i{display:inline-block;width:14px;height:14px;border-radius:3px;vertical-align:-2px;margin-right:6px}
.list{border-top:2px solid var(--ink)}
.f{display:grid;grid-template-columns:3.2em minmax(0,10em) minmax(0,1fr);gap:clamp(12px,2.4vw,28px);
padding:20px 0;border-bottom:1px solid var(--rule);scroll-margin-top:12px}
.f .no{font-size:15px;font-weight:800;color:var(--ink2);padding-top:4px}
.f .fig b{display:block;font-size:clamp(26px,3.2vw,38px);font-weight:800;line-height:1;letter-spacing:-.02em;
white-space:nowrap}
.f.applies .fig b{color:var(--red)}.f.cannot .fig b{color:var(--grey)}
.chip{display:inline-block;font-size:12.5px;font-weight:700;border-radius:3px;padding:1px 7px;margin-top:8px}
.applies .chip{background:var(--red);color:#fff}.clear .chip{border:1px solid var(--ink)}
.cannot .chip{border:1px dashed var(--grey);color:var(--ink2)}
.f h3{font-size:19px;line-height:1.25;margin:0 0 6px;font-weight:800}
.f p{margin:0 0 8px}
.f .do{font-size:15px;color:var(--ink2)}
.f .do b{color:var(--ink)}
.arms{columns:3 16em;column-gap:28px;font-size:14px;color:var(--ink2);margin:0;padding:0;list-style:none}
.arms li{break-inside:avoid;padding:3px 0;border-bottom:1px solid var(--rule)}
footer{color:var(--ink2);font-size:14px;border-top:1px solid var(--rule)}
code{font-size:14px;overflow-wrap:anywhere}
@media (max-width:760px){.board{grid-template-columns:repeat(5,minmax(0,1fr))}}
@media (max-width:640px){.f{grid-template-columns:2.2em minmax(0,1fr)}.f .fig,.f .body{grid-column:2}}
"""


def findings_html(result: dict, sources: Sequence[str] = (), title: str = "Findings check") -> str:
    """The check as one page, with no script: a board of the ten findings coloured by whether each applies, is
    clear, or cannot be told here; then each with your numbers and what to do."""
    from html import escape as e
    rows = result["findings"]
    cls = {APPLIES: "applies", CLEAR: "clear", CANNOT: "cannot"}
    board = "".join(f'<a class="tile {cls[r["status"]]}" href="#f{r["no"]}" title="{e(r["title"])}">'
                    f'<span class="n">{r["no"]:02d}</span><span class="v">{e(r["figure"])}</span>'
                    f'<span class="s">{e(r["status"])}</span></a>' for r in rows)
    items = "".join(
        f'<div class="f {cls[r["status"]]}" id="f{r["no"]}"><div class="no">{r["no"]:02d}</div>'
        f'<div class="fig"><b>{e(r["figure"])}</b><span class="chip">{e(r["status"])}</span></div>'
        f'<div class="body"><h3>{e(r["title"])}</h3><p>{e(r["yours"])}</p>'
        f'<p class="do"><b>{"Do" if r["status"] == APPLIES else "If it comes up"}:</b> {e(r["do"])}.</p></div></div>'
        for r in rows)
    c = result["counts"]
    head = (f"{c[APPLIES]} of the 10 findings apply to these runs" if c[APPLIES] else
            "None of the 10 findings applies to these runs")
    lede = (f"{result['runs']} run(s) read ({result['failed']} failed, {result['passed']} passed"
            + (f", {result['ungraded']} ungraded" if result["ungraded"] else "") + f"), in {len(result['arms'])} "
            f"arm(s)" + (f", with {result['evolutions']} self-evolve record(s)" if result["evolutions"] else "")
            + f". {c[CLEAR]} finding(s) checked clear, {c[CANNOT]} cannot be told from these runs. Every number "
              f"is a count or an exact test over the runs given.")
    arms_list = "".join(f"<li>{e(k)} <b>{v}</b></li>" for k, v in sorted(result["arms"].items()))
    src = ", ".join(f"<code>{e(str(s))}</code>" for s in sources)
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(title)}</title>'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="claude:viewport-fit-sides" content="cover">'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@100..125,400..800'
        f'&display=swap"><style>{_CSS}</style></head><body>'
        f'<header class="band top"><div class="wrap"><h1>{e(head)}</h1><p class="lede">{e(lede)}</p>'
        f'<div class="board">{board}</div><div class="key"><span><i style="background:var(--red)"></i>applies here'
        '</span><span><i style="border:2px solid var(--ink)"></i>checked, clear</span><span><i style="border:2px '
        'dashed var(--grey)"></i>cannot be told from these runs</span></div></div></header>'
        f'<section class="band"><div class="wrap"><h2>Each finding, in your runs</h2><div class="list">{items}</div>'
        '</div></section>'
        f'<section class="band"><div class="wrap"><h2>The arms read</h2><ul class="arms">{arms_list}</ul></div>'
        '</section>'
        '<footer class="band"><div class="wrap">Made by <code>agentdiff findings</code>' + (f" from {src}" if src
                                                                                        else "")
        + ". The findings and how each was first measured: docs/FINDINGS.md. No script.</div></footer>"
        "</body></html>")
