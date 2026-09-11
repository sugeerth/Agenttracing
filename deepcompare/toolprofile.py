"""Tool behaviour: how each agent used each tool, and what to tell the
next prompt about it.

The statistics settle who won; this reads *how*: per tool and per run,
the calls, the distinct inputs and the repeats, the longest run of
identical calls, errors, wasted calls and seconds, the latency, which
sub-agents touched the tool, where it first and last appeared, whether
it sat on the fault's path or at the decisive step, whether its results
fed the answer, and whether it reaches outside.  Every number is a count
or a sum over the steps.

The contrast between the two runs yields **prompt suggestions** — the
sentences a person would add to the next prompt, each derived from one
rule over the tool profiles and carrying its evidence (steps, counts,
the input repeated).  A suggestion is a hypothesis until a replay flips
the outcome, and says so, like :mod:`deepcompare.feedback`.

A **dossier** is the same profile for one tool, both runs side by side,
for the page to open on demand.
"""

from __future__ import annotations

import re
from typing import Any, Optional

TOOLISH = ("tool_call", "search", "retrieve", "read")
EXTERNAL = re.compile(r"(web|http|fetch|curl|browser|open_page|search|send_|email|slack|api|scrape|download)", re.I)
VERIFY = re.compile(r"(test|verify|check|lint|validate|assert|build|compile)", re.I)
HYPOTHESIS = "suggested — a hypothesis until a replay flips the outcome"
MAX_SUGGESTIONS = 8


def _norm(text: Any) -> str:
    return " ".join(str("" if text is None else text).split())


def _short(text: Any, n: int = 120) -> str:
    text = _norm(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def _agent_of(step: dict, root: str) -> str:
    sp = step.get("span")
    return str(sp.get("agent")) if isinstance(sp, dict) and sp.get("agent") else root


def _fmt_s(v: float) -> str:
    return f"{v:.0f}s" if v >= 10 else f"{v:.1f}s"


def profile_run(report: dict, side: str) -> dict:
    """The tool profile of one run: ``{"measurable", "agent", "tools":
    {name: stats}, "order": [names by calls], "totals": {...}}``."""
    run = report.get(side) or {}
    steps = list(run.get("steps") or [])
    root = str(((run.get("agent") or {}).get("name")) or side)
    timing = {r.get("index"): r for r in (((report.get("timing") or {}).get(side) or {}).get("steps") or [])}
    happened = {w.get("step"): w for w in (((report.get("reading") or {}).get(side) or {}).get("what_happened") or []) if isinstance(w, dict)}
    attribution = report.get("attribution") or {}
    fault = set(i for i in (attribution.get("chain") or []) if isinstance(i, int)) if attribution.get("failed_agent") == side else set()
    diag = report.get("diagnosis") or {}
    decisive = ((diag.get("decisive_step") or {}).get("step")) if diag.get("subject") == side else None
    tools: dict = {}
    last_by_tool: dict = {}   # tool → (input key, run length, run start index): a retry loop may think or write between calls
    for i, s in enumerate(steps):
        if s.get("type") not in TOOLISH:
            continue
        name = str(s.get("name") or "?")
        idx = s.get("index", i)
        t = tools.setdefault(name, {
            "name": name, "calls": 0, "inputs": {}, "repeats": 0, "max_identical_run": 1, "identical_run_at": None,
            "errors": 0, "wasted_calls": 0, "wasted_s": 0.0, "seconds": 0.0, "latencies": [], "agents": {},
            "first_step": idx, "last_step": idx, "fault_calls": 0, "decisive": False, "fed_answer": 0,
            "effects": {}, "external": bool(EXTERNAL.search(name)), "samples": {"inputs": [], "outputs": []},
            "steps": [],
        })
        key = _norm(s.get("input"))
        t["calls"] += 1
        if key in t["inputs"]:
            t["repeats"] += 1
        t["inputs"][key] = t["inputs"].get(key, 0) + 1
        last_key, run_len, run_from = last_by_tool.get(name, (None, 0, idx))
        if key == last_key:
            run_len += 1
        else:
            run_len, run_from = 1, idx
        last_by_tool[name] = (key, run_len, run_from)
        if run_len > t["max_identical_run"]:
            t["max_identical_run"] = run_len
            t["identical_run_at"] = {"from": run_from, "to": idx, "input": _short(key, 80)}
        row = timing.get(idx) or {}
        lat = row.get("latency_s") if isinstance(row.get("latency_s"), (int, float)) else (s.get("latency_s") if isinstance(s.get("latency_s"), (int, float)) else 0.0)
        t["seconds"] += float(lat or 0.0)
        t["latencies"].append(float(lat or 0.0))
        if s.get("error"):
            t["errors"] += 1
        if row.get("wasted"):
            t["wasted_calls"] += 1
            t["wasted_s"] += float(lat or 0.0)
        agent = _agent_of(s, root)
        t["agents"][agent] = t["agents"].get(agent, 0) + 1
        t["last_step"] = idx
        if idx in fault:
            t["fault_calls"] += 1
        if decisive is not None and idx == decisive:
            t["decisive"] = True
        if (happened.get(idx) or {}).get("feeds_answer"):
            t["fed_answer"] += 1
        eff = s.get("effect") or "undeclared"
        t["effects"][eff] = t["effects"].get(eff, 0) + 1
        if len(t["samples"]["inputs"]) < 3 and _short(key) not in t["samples"]["inputs"]:
            t["samples"]["inputs"].append(_short(key))
        if len(t["samples"]["outputs"]) < 2 and s.get("output"):
            t["samples"]["outputs"].append(_short(s.get("output"), 160))
        t["steps"].append({"step": idx, "error": bool(s.get("error")), "wasted": bool(row.get("wasted")), "agent": agent, "seconds": round(float(lat or 0.0), 4)})
    for t in tools.values():
        lats = t.pop("latencies")
        t["distinct_inputs"] = len(t["inputs"])
        t.pop("inputs")
        t["mean_s"] = round(t["seconds"] / t["calls"], 4) if t["calls"] else 0.0
        t["max_s"] = round(max(lats), 4) if lats else 0.0
        t["seconds"] = round(t["seconds"], 4)
        t["wasted_s"] = round(t["wasted_s"], 4)
        t["wasted_share"] = round(t["wasted_calls"] / t["calls"], 4) if t["calls"] else 0.0
        t["error_rate"] = round(t["errors"] / t["calls"], 4) if t["calls"] else 0.0
        t["agents"] = dict(sorted(t["agents"].items(), key=lambda kv: (-kv[1], kv[0])))
    order = sorted(tools, key=lambda n: (-tools[n]["calls"], n))
    writes = [t for t in tools.values() if t["effects"].get("write")]
    totals = {
        "tool_calls": sum(t["calls"] for t in tools.values()), "distinct_tools": len(tools),
        "repeats": sum(t["repeats"] for t in tools.values()), "errors": sum(t["errors"] for t in tools.values()),
        "wasted_calls": sum(t["wasted_calls"] for t in tools.values()),
        "external_calls": sum(t["calls"] for t in tools.values() if t["external"]),
        "agents_touching": len({a for t in tools.values() for a in t["agents"]}),
        "last_write_step": max((s["step"] for t in writes for s in t["steps"]), default=None),
    }
    return {"measurable": bool(tools), "agent": root, "tools": tools, "order": order, "totals": totals}


def _failing_side(report: dict) -> Optional[str]:
    subject = (report.get("diagnosis") or {}).get("subject")
    if subject in ("a", "b"):
        return subject
    sa = bool(((report.get("a") or {}).get("outcome") or {}).get("success"))
    sb = bool(((report.get("b") or {}).get("outcome") or {}).get("success"))
    if sa != sb:
        return "b" if sa else "a"
    return None


def suggest(report: dict, pa: dict, pb: dict) -> list:
    """Prompt suggestions from the contrast of the two tool profiles.
    Each carries ``text``, ``kind``, ``tool``, ``for`` (the agent it is
    addressed to), ``evidence`` and ``status``; at most
    :data:`MAX_SUGGESTIONS`, the strongest evidence first."""
    failing = _failing_side(report)
    if failing is None:
        return []
    passing = "b" if failing == "a" else "a"
    pf, pp = (pa, pb) if failing == "a" else (pb, pa)
    fname, pname = pf["agent"], pp["agent"]
    out: list = []

    def add(kind: str, tool: str, text: str, weight: float, evidence: dict) -> None:
        out.append({"kind": kind, "tool": tool, "for": fname, "text": text, "weight": round(weight, 3),
                    "evidence": evidence, "status": HYPOTHESIS})

    for name, t in pf["tools"].items():
        other = pp["tools"].get(name)
        # 1. identical retries
        if t["max_identical_run"] >= 3 and (other is None or other["max_identical_run"] < t["max_identical_run"]):
            at = t["identical_run_at"] or {}
            add("identical_retries", name,
                f"Do not call {name} again with the same input: after two identical results, change the arguments or take another route "
                f"({fname} repeated `{at.get('input', '')}` {t['max_identical_run']}× in a row at steps {at.get('from')}–{at.get('to')}; "
                f"{pname} " + (f"at most {other['max_identical_run']}×" if other else "never called it") + ").",
                3 + t["max_identical_run"] * 0.5, {"steps": [at.get("from"), at.get("to")], "run": t["max_identical_run"], "input": at.get("input"),
                                                    "other_max_run": other["max_identical_run"] if other else 0})
        # 3. unproductive tool
        if t["calls"] >= 3 and t["wasted_share"] >= 0.6 and (other is None or other["wasted_share"] < 0.3):
            add("unproductive_tool", name,
                f"Stop leaning on {name} for this task: {t['wasted_calls']} of {t['calls']} calls returned nothing new "
                f"({_fmt_s(t['wasted_s'])} wasted, first at step {t['first_step']}); "
                + (f"{pname} used it {other['calls']}× with {other['wasted_calls']} wasted." if other else f"{pname} did without it."),
                2 + t["wasted_share"], {"wasted_calls": t["wasted_calls"], "calls": t["calls"], "wasted_s": t["wasted_s"], "first_step": t["first_step"]})
        # 4. errors
        if t["errors"] >= 2 and t["error_rate"] >= 0.3:
            first_err = next((s["step"] for s in t["steps"] if s["error"]), None)
            add("tool_errors", name,
                f"Check the arguments to {name} before calling it: {t['errors']} of {t['calls']} calls errored (first at step {first_err}"
                + (f", {pname} had {other['errors']}" if other else "") + ").",
                2 + t["error_rate"], {"errors": t["errors"], "calls": t["calls"], "first_error_step": first_err})
    # 2. a tool only the passing run used, and it fed the answer or was rarely wasted
    for name, o in pp["tools"].items():
        if name in pf["tools"]:
            continue
        if o["fed_answer"] > 0 or (o["calls"] >= 1 and o["wasted_share"] < 0.5):
            add("missing_tool", name,
                f"Use {name}: {pname} called it {o['calls']}× (first at step {o['first_step']}"
                + (f", {o['fed_answer']} result(s) fed its answer" if o["fed_answer"] else "") + f"); {fname} never did.",
                1.5 + min(o["calls"], 5) * 0.2 + o["fed_answer"] * 0.5, {"calls": o["calls"], "first_step": o["first_step"], "fed_answer": o["fed_answer"]})
    # 5. verify after the last write
    lw = pf["totals"].get("last_write_step")
    if lw is not None:
        verified = any(VERIFY.search(n) and any(s["step"] > lw and not s["error"] for s in t["steps"]) for n, t in pf["tools"].items())
        if not verified:
            their = next((n for n, t in pp["tools"].items() if VERIFY.search(n)), None)
            add("verify_after_write", their or "a verification tool",
                f"After the last write (step {lw}), verify before answering" + (f" with {their} — {pname} did." if their else "."),
                2.5, {"last_write_step": lw, "passing_verifier": their})
    # 6. outside calls
    fe, pe = pf["totals"]["external_calls"], pp["totals"]["external_calls"]
    if fe >= 4 and fe >= 2 * max(pe, 1):
        names = ", ".join(n for n in pf["order"] if pf["tools"][n]["external"])[:80]
        add("external_overuse", names,
            f"Limit calls that reach outside: {fname} made {fe} ({names}) against {pname}'s {pe}; prefer the sources already in hand.",
            1.5 + min(fe / max(pe, 1), 5) * 0.2, {"external_calls": fe, "other_external_calls": pe})
    out.sort(key=lambda s: -s["weight"])
    return out[:MAX_SUGGESTIONS]


def tool_pair(report: dict) -> dict:
    """Both runs' tool profiles, the per-tool contrast, the prompt
    suggestions, and a narrative."""
    pa, pb = profile_run(report, "a"), profile_run(report, "b")
    names = sorted(set(pa["tools"]) | set(pb["tools"]),
                   key=lambda n: (-((pa["tools"].get(n) or {}).get("calls", 0) + (pb["tools"].get(n) or {}).get("calls", 0)), n))
    rows = []
    for n in names:
        a, b = pa["tools"].get(n), pb["tools"].get(n)
        rows.append({"name": n, "a": a, "b": b, "delta_calls": (a["calls"] if a else 0) - (b["calls"] if b else 0),
                     "only": "a" if a and not b else "b" if b and not a else None,
                     "agents": sorted(set((a or {}).get("agents", {})) | set((b or {}).get("agents", {})))})
    suggestions = suggest(report, pa, pb)
    return {"version": 1, "a": pa, "b": pb, "tools": rows, "suggestions": suggestions,
            "narrative": _narrative(pa, pb, rows, suggestions)}


def _narrative(pa: dict, pb: dict, rows: list, suggestions: list) -> str:
    if not (pa["measurable"] or pb["measurable"]):
        return "Neither run called a tool."
    ta, tb = pa["totals"], pb["totals"]
    parts = [f"{pa['agent']} made {ta['tool_calls']} tool call(s) over {ta['distinct_tools']} tool(s) ({ta['repeats']} repeat(s), {ta['errors']} error(s), {ta['wasted_calls']} wasted); "
             f"{pb['agent']} {tb['tool_calls']} over {tb['distinct_tools']} ({tb['repeats']} repeat(s), {tb['errors']} error(s), {tb['wasted_calls']} wasted)"]
    only_a = [r["name"] for r in rows if r["only"] == "a"]
    only_b = [r["name"] for r in rows if r["only"] == "b"]
    if only_a or only_b:
        parts.append("only " + "; ".join(x for x in [
            f"{pa['agent']} used {', '.join(only_a)}" if only_a else "", f"{pb['agent']} used {', '.join(only_b)}" if only_b else ""] if x))
    shared = [r for r in rows if r["a"] and r["b"]]
    if shared:
        widest = max(shared, key=lambda r: abs(r["delta_calls"]))
        if widest["delta_calls"]:
            more = pa["agent"] if widest["delta_calls"] > 0 else pb["agent"]
            parts.append(f"the widest gap is {widest['name']}: {more} called it {abs(widest['delta_calls'])} time(s) more")
    touched = max(rows, key=lambda r: len(r["agents"])) if rows else None
    if touched and len(touched["agents"]) > 1:
        parts.append(f"{touched['name']} was touched by {len(touched['agents'])} agent(s)")
    if suggestions:
        parts.append(f"{len(suggestions)} suggestion(s) for the next prompt, the first: {suggestions[0]['text'].split(':')[0]}")
    return "; ".join(parts) + "."


def dossier(report: dict, name: str) -> dict:
    """One tool, both runs: the page's on-demand card."""
    pair = report.get("tools_profile") or tool_pair(report)
    row = next((r for r in pair["tools"] if r["name"] == name), None)
    if row is None:
        return {"name": name, "found": False}
    return {"name": name, "found": True, "a": row["a"], "b": row["b"], "agents": row["agents"],
            "suggestions": [s for s in pair["suggestions"] if s["tool"] == name]}
