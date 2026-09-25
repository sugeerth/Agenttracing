"""Agent as a judge, across runs: a panel that reads a corpus and synthesises.

:mod:`deepcompare.harness.agentjudge` answers *did this run do X*. Useful,
and the wrong shape for the question a reader actually has after forty
runs, which is never about one run:

* What is wrong with this **agent**, as opposed to this run?
* Why does one side succeed where the other fails on the same task?
* What do the failures have in common, and is it one cause or five?

Those questions need a judge that can hold many trajectories at once, and
the single-trace judge cannot be made to answer them by being run forty
times: forty independent verdicts are forty anecdotes, and the thing worth
knowing is the pattern *between* them. So this one gets tools over the
**corpus** — list the runs, contrast the two sides of a task, descend into
any single run with the per-trace instruments — and is asked for a
synthesis rather than a grade.

**Every finding must cite, and every citation is checked.** This is the
part that makes a synthesis worth reading rather than worth believing. The
panel returns findings with ``cites`` — a run, a step index, and a
fragment it says is there — and :func:`verify` then goes and looks. A run
that does not exist, a step index out of range, a quotation the step does
not contain: the finding is marked ``unsupported`` with the reason and is
**kept out of the synthesis**. Nothing about that check involves a model.
It is string containment against the trace, and it is the difference
between a fluent paragraph and a finding.

A synthesis over a large corpus is a long task — tens of minutes to hours
of turns — so it checkpoints after every finding and resumes from the
checkpoint. An agent asked to read four hundred runs will be interrupted;
losing an hour of reading to a network blip is a property of the harness,
not of the model.

Three refusals, each for the same reason as in the single-trace judge:

* **No golden set.** The panel sees the runs and what the deterministic
  engine says about them. It never sees which failure a task is known to
  carry, because the point of asking it is to find out what it finds.
* **No memory between findings.** Each is produced in its own loop.
* **It cannot move a number.** The synthesis is recorded beside the card.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Optional

from .agent import Tool, run_task
from .agentjudge import trace_tools
from .providers import Provider, ProviderError

#: turns the panel gets to produce one finding.  Higher than the
#: single-trace judge's: reading across runs is more look-ups, not more
#: thinking, and a panel that runs out of turns mid-look reports nothing.
TURNS = 24

#: how many runs `runs()` will list at once
LIST_CAP = 40
#: how long a cited fragment may be before it is truncated for matching
QUOTE_CAP = 160

SYSTEM = (
    "You are reading a corpus of recorded AI agent runs to work out what is systematically wrong — "
    "not what went wrong in one run. You cannot see the runs up front; use the tools.\n\n"
    "corpus() tells you what is here. runs(...) lists and filters them. contrast(task) puts the two "
    "agents' runs of one task side by side. open(run) then graph/locate/read/flags let you go into a "
    "single run the way you would read a file.\n\n"
    "Look for the pattern across runs, and prefer one cause that explains five failures to five "
    "separate observations. An agent saying a thing was done is not evidence that it was.\n\n"
    "EVERY claim must cite the trace it came from, and the citations are checked mechanically "
    "against the recorded steps: a run that does not exist, a step out of range or a quotation the "
    "step does not contain will have the finding removed. Quote exactly what the step says — copy "
    "the text, do not paraphrase it.\n\n"
    "When you are certain, reply with JSON only and no tool call:\n"
    '{"claim": "one or two sentences", "affects": ["task or agent names"], '
    '"cites": [{"run": "<run key from runs()>", "step": <index>, "quote": "<exact text from that step>"}]}'
)


def _key(trace: dict) -> str:
    task = (trace.get("task") or {}).get("id") or "task"
    agent = (trace.get("agent") or {}).get("name") or "agent"
    return f"{task}__{agent}"


def corpus_tools(traces: list, policy: Optional[dict] = None) -> list:
    """The panel's instruments over a whole corpus.

    Read-only and pure. ``open`` is the seam: it hands back the
    single-trace instruments for one run, so the panel reads across runs
    and then descends, rather than having two vocabularies.
    """
    by_key = {_key(t): t for t in traces}

    def corpus() -> dict:
        agents: dict = {}
        tasks: dict = {}
        for key, t in by_key.items():
            a = (t.get("agent") or {}).get("name") or "?"
            task = (t.get("task") or {}).get("id") or "?"
            outcome = (t.get("outcome") or {})
            row = agents.setdefault(a, {"runs": 0, "succeeded": 0, "failed": 0, "ungraded": 0, "steps": 0})
            row["runs"] += 1
            row["steps"] += len(t.get("steps") or [])
            if outcome.get("success") is True:
                row["succeeded"] += 1
            elif outcome.get("success") is False:
                row["failed"] += 1
            else:
                row["ungraded"] += 1
            tasks.setdefault(task, []).append(a)
        return {"runs": len(by_key), "agents": agents,
                "tasks": len(tasks),
                "tasks_with_both_sides": sum(1 for v in tasks.values() if len(set(v)) > 1),
                "note": "runs(...) lists them; a run key is <task>__<agent>"}

    def runs(agent: str = "", task: str = "", failed: str = "") -> dict:
        want_failed = str(failed).lower() in ("1", "true", "yes")
        want_passed = str(failed).lower() in ("0", "false", "no")
        rows = []
        for key, t in sorted(by_key.items()):
            if agent and (t.get("agent") or {}).get("name") != agent:
                continue
            if task and task not in ((t.get("task") or {}).get("id") or ""):
                continue
            ok = (t.get("outcome") or {}).get("success")
            if want_failed and ok is not False:
                continue
            if want_passed and ok is not True:
                continue
            rows.append({"run": key, "agent": (t.get("agent") or {}).get("name"),
                         "task": (t.get("task") or {}).get("id"),
                         "success": ok, "steps": len(t.get("steps") or [])})
        return {"found": len(rows), "shown": min(len(rows), LIST_CAP), "runs": rows[:LIST_CAP],
                "note": "narrow with agent=, task= or failed=true" if len(rows) > LIST_CAP else ""}

    def contrast(task: str) -> dict:
        """The runs of one task, side by side, with where they part."""
        sides = [t for k, t in sorted(by_key.items()) if ((t.get("task") or {}).get("id") or "") == task]
        if len(sides) < 2:
            return {"task": task, "error": "fewer than two runs of that task; runs(task=…) lists what is here"}
        rows = []
        for t in sides[:2]:
            steps = t.get("steps") or []
            rows.append({"run": _key(t), "agent": (t.get("agent") or {}).get("name"),
                         "success": (t.get("outcome") or {}).get("success"),
                         "steps": len(steps),
                         "answer": str((t.get("outcome") or {}).get("answer") or "")[:400]})
        first = None
        a_steps, b_steps = (sides[0].get("steps") or []), (sides[1].get("steps") or [])
        for i in range(min(len(a_steps), len(b_steps))):
            sa, sb = a_steps[i], b_steps[i]
            if (sa.get("name"), str(sa.get("input"))) != (sb.get("name"), str(sb.get("input"))):
                first = {"at": i,
                         "a": f"[{sa.get('index')}] {sa.get('name')}: {str(sa.get('input'))[:120]}",
                         "b": f"[{sb.get('index')}] {sb.get('name')}: {str(sb.get('input'))[:120]}"}
                break
        return {"task": task, "sides": rows, "first_divergence": first,
                "note": "step-for-step until the first differing call; open(run) reads either in full"}

    def open_run(run: str) -> dict:
        """The per-run instruments, for one run key."""
        trace = by_key.get(run)
        if not trace:
            return {"error": f"no run {run!r}; runs() lists the keys"}
        tools = {t.name: t for t in trace_tools(trace, policy)}
        return {"run": run, "graph": tools["graph"].fn(), "flags": tools["flags"].fn(),
                "note": "locate(term, run=…) and read(start, end, run=…) go further into this run"}

    def locate(term: str, run: str = "") -> dict:
        if run:
            trace = by_key.get(run)
            if not trace:
                return {"error": f"no run {run!r}"}
            return dict({t.name: t for t in trace_tools(trace, policy)}["locate"].fn(term), run=run)
        hits = []
        needle = str(term or "").lower()
        for key, t in sorted(by_key.items()):
            for s in (t.get("steps") or []):
                hay = f"{s.get('name') or ''} {s.get('input') or ''} {s.get('output') or ''}".lower()
                if needle and needle in hay:
                    hits.append({"run": key, "index": s.get("index"),
                                 "excerpt": f"{s.get('name')}: {str(s.get('input'))[:110]}"})
                    break            # one hit per run keeps the shape of the corpus visible
        return {"term": term, "runs_mentioning": len(hits), "hits": hits[:LIST_CAP]}

    def read(start: int, end: int, run: str = "") -> dict:
        trace = by_key.get(run)
        if not trace:
            return {"error": f"no run {run!r}; read needs run=<key>"}
        return dict({t.name: t for t in trace_tools(trace, policy)}["read"].fn(start, end), run=run)

    def flags(run: str = "") -> dict:
        trace = by_key.get(run)
        if not trace:
            return {"error": f"no run {run!r}; flags needs run=<key>"}
        return dict({t.name: t for t in trace_tools(trace, policy)}["flags"].fn(), run=run)

    runspec = {"type": "object", "properties": {
        "agent": {"type": "string"}, "task": {"type": "string"},
        "failed": {"type": "string", "description": "true for failing runs only, false for passing"}}}
    return [
        Tool(name="corpus", fn=corpus, effect="read",
             description="What is in this corpus: how many runs, which agents, how they scored.",
             parameters={"type": "object", "properties": {}}),
        Tool(name="runs", fn=runs, effect="read",
             description="List the runs, filtered by agent, task or outcome.", parameters=runspec),
        Tool(name="contrast", fn=contrast, effect="read",
             description="Two agents' runs of the same task side by side, with the first call they differ on.",
             parameters={"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]}),
        Tool(name="open", fn=open_run, effect="read",
             description="Open one run: its shape, and where the deterministic analysis flags it.",
             parameters={"type": "object", "properties": {"run": {"type": "string"}}, "required": ["run"]}),
        Tool(name="locate", fn=locate, effect="read",
             description="Find a term — across the corpus, or inside one run with run=<key>.",
             parameters={"type": "object", "properties": {
                 "term": {"type": "string"}, "run": {"type": "string"}}, "required": ["term"]}),
        Tool(name="read", fn=read, effect="read",
             description="Read a range of steps of one run, in full.",
             parameters={"type": "object", "properties": {
                 "start": {"type": "integer"}, "end": {"type": "integer"}, "run": {"type": "string"}},
                 "required": ["start", "end", "run"]}),
        Tool(name="flags", fn=flags, effect="read",
             description="Where the deterministic analysis flags one run, with the reason each fired.",
             parameters={"type": "object", "properties": {"run": {"type": "string"}}, "required": ["run"]}),
    ]


# --------------------------------------------------------------- verifying

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def verify(finding: dict, traces: list) -> dict:
    """Check a finding's citations against the traces, and say what failed.

    No model is involved: a citation names a run, a step and a fragment,
    and either that step contains that fragment or it does not. A finding
    whose citations do not check out is `supported: False` with a reason
    per citation, and the caller keeps it out of the synthesis.
    """
    by_key = {_key(t): t for t in traces}
    cites = finding.get("cites") or []
    checked = []
    for cite in cites if isinstance(cites, list) else []:
        if not isinstance(cite, dict):
            checked.append({"cite": cite, "ok": False, "why": "not a citation"})
            continue
        run = str(cite.get("run") or "")
        trace = by_key.get(run)
        if trace is None:
            checked.append({"cite": cite, "ok": False, "why": f"no run named {run!r} in this corpus"})
            continue
        index = cite.get("step")
        step = next((s for s in (trace.get("steps") or []) if s.get("index") == index), None)
        if step is None:
            checked.append({"cite": cite, "ok": False, "why": f"run {run} has no step {index}"})
            continue
        quote = _norm(cite.get("quote"))[:QUOTE_CAP]
        if not quote:
            checked.append({"cite": cite, "ok": False, "why": "the citation quotes nothing"})
            continue
        hay = _norm(f"{step.get('name')} {step.get('input')} {step.get('output')} {step.get('note')}")
        ok = quote in hay
        checked.append({"cite": cite, "ok": ok,
                        "why": "" if ok else f"step {index} of {run} does not contain that text"})
    good = [c for c in checked if c["ok"]]
    return {
        "citations": checked,
        "cited": len(checked),
        "verified": len(good),
        "supported": bool(checked) and len(good) == len(checked),
        "reason": ("" if checked and len(good) == len(checked)
                   else "the finding cites nothing" if not checked
                   else "; ".join(c["why"] for c in checked if not c["ok"])),
        "basis": ("each citation names a run, a step and a fragment; the fragment either appears in that "
                  "step's call, result or note or it does not. No model is involved in this check"),
    }


# ----------------------------------------------------------------- panel

def _parse(answer: str) -> Optional[dict]:
    m = re.search(r"\{[\s\S]*\}", answer or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict) or not str(data.get("claim") or "").strip():
        return None
    return {"claim": str(data["claim"])[:800],
            "affects": [str(x)[:80] for x in (data.get("affects") or [])][:20],
            "cites": data.get("cites") or []}


def one_finding(traces: list, provider: Provider, question: str, *,
                policy: Optional[dict] = None, turns: int = TURNS, out_dir=None) -> dict:
    """Ask the panel one question, and check what it answers."""
    task = {"id": "panel:" + re.sub(r"\W+", "_", question)[:50], "prompt": question}
    tools = corpus_tools(traces, policy)
    looked = {"calls": 0, "by_tool": {}}
    wrapped = []
    for tool in tools:
        def count(fn=tool.fn, name=tool.name):
            def inner(*a, **kw):
                looked["calls"] += 1
                looked["by_tool"][name] = looked["by_tool"].get(name, 0) + 1
                return fn(*a, **kw)
            return inner
        wrapped.append(Tool(name=tool.name, fn=count(), description=tool.description,
                            parameters=tool.parameters, effect=tool.effect))
    try:
        run = run_task(provider, task, wrapped, agent="panel",
                       budget={"max_steps": turns}, grader=lambda answer, t: True,
                       system_prompt=SYSTEM, out_dir=out_dir)
        answer = str(((run or {}).get("outcome") or {}).get("answer") or "")
        error = None
    except ProviderError as exc:
        run, answer, error = None, "", f"provider error: {exc}"
    parsed = _parse(answer)
    if not error and not parsed:
        error = "the panel did not return the JSON asked for"
    check = verify(parsed or {}, traces) if parsed else None
    return {
        "question": question,
        "claim": (parsed or {}).get("claim"),
        "affects": (parsed or {}).get("affects") or [],
        "verification": check,
        "supported": bool(check and check["supported"]),
        "error": error,
        "turns": len((run or {}).get("steps") or []),
        "tool_calls": looked["calls"],
        "tools_used": dict(sorted(looked["by_tool"].items())),
        "looked_before_answering": looked["calls"] > 0,
        "trace_id": (run or {}).get("trace_id"),
        "raw": answer[:800],
    }


#: the questions a corpus can be asked that a single run cannot answer
QUESTIONS = [
    "What is the single most common way the failing runs in this corpus go wrong? "
    "Name the cause, not the symptom.",
    "On the tasks where one agent succeeds and the other fails, what does the failing side do "
    "differently, and at which step does it first matter?",
    "Is there anything the runs that passed did that was nevertheless wrong — something a grade "
    "would not catch?",
    "What does this corpus show about the agents' use of tools that a per-run reading would miss?",
]


def convene(traces: list, provider_factory: Callable[[], Provider], *,
            questions: Optional[list] = None, policy: Optional[dict] = None,
            turns: int = TURNS, checkpoint=None, on_finding=None) -> dict:
    """Put the corpus to a panel and return a verified synthesis.

    Each question is asked in its own loop with its own provider — no
    memory between them, for the reason the single-trace judge gives. A
    finding whose citations do not check out is kept, marked, and left out
    of the synthesis.

    ``checkpoint`` is a path: the result so far is written after every
    finding and read back on a later call, so a long reading survives an
    interruption. ``on_finding`` is called with each finding as it lands,
    for a caller that wants to print progress.
    """
    from pathlib import Path
    questions = list(questions or QUESTIONS)
    done: list = []
    path = Path(checkpoint) if checkpoint else None
    if path and path.is_file():
        try:
            done = json.loads(path.read_text(encoding="utf-8")).get("findings") or []
        except (OSError, ValueError):
            done = []
    asked = {f.get("question") for f in done}

    for question in questions:
        if question in asked:
            continue
        finding = one_finding(traces, provider_factory(), question,
                              policy=policy, turns=turns, out_dir=None)
        done.append(finding)
        if on_finding:
            on_finding(finding)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"findings": done}, indent=1, ensure_ascii=False) + "\n",
                            encoding="utf-8")

    supported = [f for f in done if f["supported"]]
    dropped = [f for f in done if not f["supported"] and not f["error"]]
    probe = provider_factory()
    return {
        "kind": "panel",
        "model": getattr(probe, "model", "") or getattr(probe, "name", "panel"),
        "runs": len(traces),
        "asked": len(done),
        "synthesis": [{"claim": f["claim"], "affects": f["affects"],
                       "cites": (f["verification"] or {}).get("citations") or []}
                      for f in supported],
        "findings": done,
        "supported": len(supported),
        "dropped_for_bad_citations": len(dropped),
        "dropped": [{"claim": f["claim"], "reason": (f["verification"] or {}).get("reason")}
                    for f in dropped],
        "errors": [f["error"] for f in done if f["error"]],
        "turns": sum(f["turns"] for f in done),
        "tool_calls": sum(f["tool_calls"] for f in done),
        "memory_between_questions": False,
        "basis": ("one loop per question with no memory between them; every claim cites a run, a step "
                  "and a fragment, and a claim whose citations do not appear in the trace is dropped "
                  "from the synthesis rather than softened"),
    }


__all__ = ["TURNS", "SYSTEM", "QUESTIONS", "corpus_tools", "verify", "one_finding", "convene"]
