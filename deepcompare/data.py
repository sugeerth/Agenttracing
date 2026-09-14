"""The data side of a run: what the agent was told, what it read, and the
chain from that to its answer.

Every other section reads what the agent *did*. This one reads what it
was *given* and what came *in*: the task prompt and the expected answer
as the trace records them; the agent's instructions when the trace (or,
for a lineage, the generation's artifacts) carries them; which model
produced which steps, from the step telemetry when a step names one and
otherwise the trace's declared ``agent.model``, with the source said
beside every attribution; the corpus — every distinct source the run
fetched, identified the way the fetches section identifies a repeat
(the tool name and the whitespace-normalised input), with the size of
what came back and a digest of it; the provenance of the answer — its
typed values (the claim extractor of :mod:`deepcompare.semantic`) traced
to the fetched outputs that carry them; and the chain data → model →
agent → answer drawn from those records.

Two measures, both stated and nothing inferred beyond them:

* **provenance overlap** of a fetched output with the answer — the share
  of the answer's typed values (money, percents, durations, versions,
  CVEs, URLs, dates, unit numbers) the output carries, a value counting
  as carried when the extractor finds the same normalised value in the
  output or the value's normalised text is a substring of the output's
  (``semantic.normalize_for_containment`` on both);
* **chain overlap** of a fetched output with a model step — the share of
  the model step's distinct normalised tokens (whitespace tokens of the
  normalised text, at least :data:`TOKEN_MIN` characters, at least one
  letter or digit) that occur among the output's. An edge is drawn at
  :data:`CHAIN_OVERLAP` or by adjacency, and says which.

A trace that carries no prompt, no model or no text is *unmeasurable*
with the reason; the parts that can still be read are still produced.
Every number is a count, a sum or one of the two measures above, with
its basis; a trace's own recorded model name is shown as recorded — it
is the trace's data — and nothing here names a model of its own.
"""

from __future__ import annotations

import difflib
import hashlib
from typing import Any, Optional

from . import sections as _sections
from ._stats import finite, mean, rounded
from ._text import join_names, num, pct, plural, run_name
from .fetches import FETCH_KINDS, QUERY_CHARS, RunView, _key as normalise_query, synthetic_of
from .section import measurable, unmeasurable
from .semantic import extract_from_text, normalize_for_containment
from .trace import Trajectory

VERSION = 1
#: the least chain overlap that draws a ``feeds`` edge on its own
CHAIN_OVERLAP = 0.2
#: how much of a step's text a level-3 bundle record keeps; the full text is one call away
TEXT_CAP = 4000
#: the shortest token the chain overlap counts
TOKEN_MIN = 3
#: the steps a model produces that are not the answer
MODEL_KINDS = ("plan", "reason")
#: how many hex characters of a SHA-256 an id or a digest keeps
ID_CHARS = 16
CHAIN_BASIS = (f"a data node feeds a model node when the share of the model step's distinct normalised tokens "
               f"(≥ {TOKEN_MIN} chars) found in the fetched output is ≥ {CHAIN_OVERLAP} (the overlap), or when the fetch "
               "is the step just before it (overlap null when only adjacent); a model node produces the fetch that "
               "follows it; the agent produces every model step and the answer; a data node reaches the answer with "
               "the share of the answer's typed values it carries")
PROVENANCE_BASIS = ("the answer's typed values (money, percent, duration, version, CVE, URL, date, unit number — "
                    "semantic.extract_from_text), each traced to every fetched output before the answer that carries "
                    "the same normalised value or contains the value's normalised text; supported = carried by at "
                    "least one fetched output; overlap per output = the share of the values it carries")
SOURCE_ID_BASIS = f"sha256 of the tool name and the whitespace-normalised input, first {ID_CHARS} hex chars"


# ------------------------------------------------------------- the measures

def tokens_of(text: Any) -> set:
    """The distinct normalised tokens of ``text`` the chain overlap counts."""
    out = set()
    for tok in normalize_for_containment(str(text or "")).split():
        if len(tok) >= TOKEN_MIN and any(c.isalnum() for c in tok):
            out.add(tok)
    return out


def containment(needle: set, hay: set) -> Optional[float]:
    """The share of ``needle`` found in ``hay``; None when ``needle`` is empty."""
    if not needle:
        return None
    return rounded(len(needle & hay) / len(needle))


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:ID_CHARS]


def source_id(name: str, query: Any) -> str:
    """The identity of a source: the tool name and the normalised input —
    the fetches section's rule for a repeat, hashed."""
    return _sha((name or "") + "\x00" + normalise_query(query))


def _cap(text: str, n: int = QUERY_CHARS) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------- reading a run

def _agent_of(run: Any) -> dict:
    """``{name, model, version, system_prompt, config, tools, harness,
    task, answer}`` from a trajectory or a dict (a raw trace or a report
    side, whose task is absent unless the caller supplies it)."""
    if isinstance(run, dict):
        agent = run.get("agent") if isinstance(run.get("agent"), dict) else {}
        task = run.get("task") if isinstance(run.get("task"), dict) else None
        outcome = run.get("outcome") if isinstance(run.get("outcome"), dict) else {}
        return {"name": run_name(run), "model": str(agent.get("model") or "") or None,
                "version": str(agent.get("version") or "") or None,
                "system_prompt": agent.get("system_prompt") if isinstance(agent.get("system_prompt"), str) else None,
                "config": agent.get("config") if isinstance(agent.get("config"), dict) else None,
                "tools": [t for t in (run.get("tools") or []) if isinstance(t, dict)],
                "harness": run.get("harness"), "task": task,
                "answer": str(outcome.get("answer") or "")}
    agent = getattr(run, "agent", None)
    task = getattr(run, "task", None)
    outcome = getattr(run, "outcome", None)
    return {"name": run_name(run), "model": str(getattr(agent, "model", "") or "") or None,
            "version": str(getattr(agent, "version", "") or "") or None,
            "system_prompt": getattr(agent, "system_prompt", None) if isinstance(getattr(agent, "system_prompt", None), str) else None,
            "config": getattr(agent, "config", None) if isinstance(getattr(agent, "config", None), dict) else None,
            "tools": [t for t in (getattr(run, "tools", None) or []) if isinstance(t, dict)],
            "harness": getattr(run, "harness", None),
            "task": task.to_dict() if task is not None and hasattr(task, "to_dict") else None,
            "answer": str(getattr(outcome, "answer", "") or "")}


def _task_block(task: Optional[dict]) -> dict:
    task = task if isinstance(task, dict) else {}
    prompt = str(task.get("prompt") or "")
    expected = task.get("expected") if isinstance(task.get("expected"), str) else None
    return {"id": str(task.get("id") or "") or None, "prompt": prompt, "prompt_chars": len(prompt),
            "expected": expected, "expected_chars": len(expected) if expected is not None else None}


def _instructions(info: dict, override: Optional[dict]) -> dict:
    """The instructions block: the caller's (a lineage's artifacts), else
    ``agent.system_prompt``, else ``agent.config.system_prompt``."""
    if isinstance(override, dict) and isinstance(override.get("system_prompt"), str):
        text = override["system_prompt"]
        return {"system_prompt": text, "source": str(override.get("source") or "lineage artifacts"), "chars": len(text)}
    if info["system_prompt"] is not None:
        return {"system_prompt": info["system_prompt"], "source": "trace.agent.system_prompt", "chars": len(info["system_prompt"])}
    config = info["config"] or {}
    if isinstance(config.get("system_prompt"), str):
        return {"system_prompt": config["system_prompt"], "source": "trace.agent.config", "chars": len(config["system_prompt"])}
    return {"system_prompt": None, "source": None, "chars": None}


def _step_model(step: Any, declared: Optional[str]) -> tuple:
    """``(model, source)`` for one step: the telemetry's name when the step
    carries one, else the trace's declared model, else nothing."""
    telemetry = step.model if isinstance(step.model, dict) else {}
    for key in ("model", "name"):
        value = telemetry.get(key)
        if isinstance(value, str) and value:
            return value, "steps[].model"
    if declared:
        return declared, "trace.agent.model"
    return None, None


def _models(steps: list, declared: Optional[str]) -> list:
    rows: dict = {}
    order: list = []
    for st in steps:
        model, source = _step_model(st, declared)
        key = (model, source)
        if key not in rows:
            rows[key] = {"model": model, "steps": 0, "kinds": {}, "tokens": 0, "temperature": None,
                         "_temps": set(), "source": source}
            order.append(key)
        row = rows[key]
        row["steps"] += 1
        row["kinds"][st.type] = row["kinds"].get(st.type, 0) + 1
        row["tokens"] += int(st.tokens) if finite(st.tokens) and st.tokens > 0 else 0
        temp = (st.model or {}).get("temperature") if isinstance(st.model, dict) else None
        if finite(temp):
            row["_temps"].add(float(temp))
    out = []
    for key in order:
        row = rows[key]
        temps = row.pop("_temps")
        row["temperature"] = next(iter(temps)) if len(temps) == 1 else None
        row["kinds"] = dict(sorted(row["kinds"].items()))
        out.append(row)
    return out


def _tools(info: dict, steps: list) -> tuple:
    declared = [{"name": str(t.get("name") or ""), "effect": t.get("effect") if t.get("effect") in ("read", "write") else None}
                for t in info["tools"] if str(t.get("name") or "")]
    used: dict = {}
    for st in steps:
        if st.type not in FETCH_KINDS:
            continue
        row = used.setdefault(st.name or "?", {"name": st.name or "?", "calls": 0, "effect_seen": []})
        row["calls"] += 1
        if st.effect in ("read", "write") and st.effect not in row["effect_seen"]:
            row["effect_seen"].append(st.effect)
    rows = sorted(used.values(), key=lambda r: (-r["calls"], r["name"]))
    for r in rows:
        r["effect_seen"] = sorted(r["effect_seen"]) or None
    return declared, rows


def corpus_of(steps: list) -> dict:
    """The distinct sources a run fetched, in first-read order."""
    sources: dict = {}
    order: list = []
    for st in steps:
        if st.type not in FETCH_KINDS:
            continue
        sid = source_id(st.name or "", st.input)
        query = normalise_query(st.input)
        if sid not in sources:
            sources[sid] = {"id": sid, "name": st.name or "", "kind": st.type, "input": _cap(query), "input_chars": len(query),
                            "output_chars": 0, "tokens": 0, "steps": [], "first_step": st.index,
                            "digest": _sha(st.output or ""), "error": None, "outputs_differ": False, "_first_output": st.output or ""}
            order.append(sid)
        row = sources[sid]
        row["steps"].append(st.index)
        row["output_chars"] += len(st.output or "")
        row["tokens"] += int(st.tokens) if finite(st.tokens) and st.tokens > 0 else 0
        if st.error is True:
            row["error"] = True
        elif st.error is False and row["error"] is None:
            row["error"] = False
        if (st.output or "") != row["_first_output"]:
            row["outputs_differ"] = True
    rows = []
    for sid in order:
        row = sources[sid]
        row.pop("_first_output")
        rows.append(row)
    fetched = sum(len(r["steps"]) for r in rows)
    return {"sources": rows, "total_chars": sum(r["output_chars"] for r in rows), "distinct": len(rows),
            "fetches": fetched, "repeated_reads": fetched - len(rows), "id_basis": SOURCE_ID_BASIS}


def _answer_text(steps: list, info: dict) -> tuple:
    """``(text, source)``: the answer step's output, else the outcome's
    answer, else the answer step's input."""
    last = steps[-1] if steps and steps[-1].type == "answer" else None
    if last is not None and (last.output or "").strip():
        return last.output, "steps[-1].output"
    if info["answer"].strip():
        return info["answer"], "outcome.answer"
    if last is not None and (last.input or "").strip():
        return last.input, "steps[-1].input"
    return "", None


def provenance_of(steps: list, answer: str, answer_source: Optional[str], sources: list) -> dict:
    """The answer's typed values traced to the fetched outputs that carry
    them; per fetched step, the share of the values it carries."""
    answer_idx = steps[-1].index if steps and steps[-1].type == "answer" else None
    atoms: list = []
    seen = set()
    for kind, value, norm in extract_from_text(answer or ""):
        if (kind, norm) in seen:
            continue
        seen.add((kind, norm))
        atoms.append({"id": f"a{len(atoms) + 1}", "kind": kind, "value": value, "normalized": norm,
                      "needle": normalize_for_containment(value), "steps": []})
    by_step: dict = {}
    name_of = {}
    for src in sources:
        for i in src["steps"]:
            name_of[i] = src
    for st in steps:
        if st.type not in FETCH_KINDS or (answer_idx is not None and st.index >= answer_idx):
            continue
        if not atoms:
            continue
        out_norms = {n for _, _, n in extract_from_text(st.output or "")}
        out_text = normalize_for_containment(st.output or "")
        carried = []
        for atom in atoms:
            if atom["normalized"] in out_norms or (atom["needle"] and atom["needle"] in out_text):
                atom["steps"].append(st.index)
                carried.append(atom["id"])
        if carried:
            by_step[st.index] = carried
    grounded_in = [{"step": i, "name": steps_by_index(steps, i).name or "", "kind": steps_by_index(steps, i).type,
                    "source": name_of[i]["id"] if i in name_of else None,
                    "overlap": rounded(len(ids) / len(atoms)), "atoms": ids,
                    "basis": f"{len(ids)} of the answer's {len(atoms)} typed values carried by this output"}
                   for i, ids in sorted(by_step.items())]
    for atom in atoms:
        atom.pop("needle")
        atom["supported"] = bool(atom["steps"])
    supported = sum(1 for a in atoms if a["supported"])
    return {"answer_chars": len(answer or ""), "answer_source": answer_source, "atoms": len(atoms), "supported": supported,
            "unsupported": len(atoms) - supported, "values": atoms, "grounded_in": grounded_in,
            "grounded_share": rounded(supported / len(atoms)) if atoms else None,
            "ungrounded_share": rounded((len(atoms) - supported) / len(atoms)) if atoms else None,
            "basis": PROVENANCE_BASIS if atoms else "the answer carries no typed value, so nothing can be traced"}


def steps_by_index(steps: list, index: int) -> Any:
    for st in steps:
        if st.index == index:
            return st
    raise KeyError(index)


def chain_of(steps: list, info: dict, instructions: dict, declared: Optional[str], provenance: dict) -> dict:
    """The graph data → model → agent → answer for one run."""
    nodes = [{"id": "agent", "kind": "agent", "step": None, "label": info["name"], "chars": instructions["chars"]}]
    edges: list = []
    answer_idx = steps[-1].index if steps and steps[-1].type == "answer" else None
    data_nodes: list = []
    for st in steps:
        if st.type in FETCH_KINDS:
            label = st.name or st.type
            query = normalise_query(st.input)
            if query:
                label += f": {_cap(query, 60)}"
            nodes.append({"id": f"data{st.index}", "kind": "data", "step": st.index, "label": label, "chars": len(st.output or "")})
            data_nodes.append((st, tokens_of(st.output)))
    model_steps = [st for st in steps if st.type in MODEL_KINDS or (answer_idx is not None and st.index == answer_idx)]
    for st in model_steps:
        model, _source = _step_model(st, declared)
        is_answer = answer_idx is not None and st.index == answer_idx
        nid = "answer" if is_answer else f"model{st.index}"
        node = {"id": nid, "kind": "answer" if is_answer else "model", "step": st.index,
                "label": model if model else "model unrecorded"}
        if is_answer:
            node["chars"] = provenance["answer_chars"]
        else:
            node["tokens"] = int(st.tokens) if finite(st.tokens) and st.tokens >= 0 else None
        nodes.append(node)
        edges.append({"from": "agent", "to": nid, "kind": "produces", "step": st.index, "overlap": None, "basis": "the agent's step"})
        needle = tokens_of(f"{st.input}\n{st.output}")
        for dst, hay in data_nodes:
            if dst.index >= st.index:
                continue
            overlap = containment(needle, hay)
            adjacent = dst.index == st.index - 1
            strong = overlap is not None and overlap >= CHAIN_OVERLAP
            if strong or adjacent:
                edges.append({"from": f"data{dst.index}", "to": nid, "kind": "feeds", "step": st.index,
                              "overlap": overlap if strong else None,
                              "basis": "overlap and adjacent" if strong and adjacent else ("overlap" if strong else "adjacent")})
    positions = {st.index: st for st in steps}
    for st in model_steps:
        nxt = positions.get(st.index + 1)
        if nxt is not None and nxt.type in FETCH_KINDS:
            edges.append({"from": f"model{st.index}", "to": f"data{nxt.index}", "kind": "produces", "step": nxt.index,
                          "overlap": None, "basis": "the fetch follows the model step"})
    if answer_idx is not None:
        for g in provenance["grounded_in"]:
            edges.append({"from": f"data{g['step']}", "to": "answer", "kind": "reaches", "step": answer_idx,
                          "overlap": g["overlap"], "basis": g["basis"]})
    feeds = sum(1 for e in edges if e["kind"] == "feeds")
    reaches = sum(1 for e in edges if e["kind"] == "reaches")
    reading = (f"{plural(len(data_nodes), 'data node')}, {plural(len(model_steps) - (1 if answer_idx is not None else 0), 'model node')}"
               f" and the answer; {plural(feeds, 'feeds edge')}"
               f" ({sum(1 for e in edges if e['kind'] == 'feeds' and e['overlap'] is not None)} by overlap), "
               f"{plural(reaches, 'data node')} {'reaches' if reaches == 1 else 'reach'} the answer with a typed value.")
    return {"nodes": nodes, "edges": edges, "reading": reading, "basis": CHAIN_BASIS}


def _empty_run(reason: str, name: str, synthetic: bool = False, task: Optional[dict] = None) -> dict:
    return unmeasurable(reason, task=_task_block(task), agent=None, models=[], corpus=None, provenance=None, chain=None,
                        synthetic=synthetic, narrative=f"{name}: {reason}, so the data side cannot be read.")


def data_run(run: Any, *, task: Optional[dict] = None, instructions: Optional[dict] = None) -> dict:
    """The data side of one run — a :class:`~deepcompare.trace.Trajectory`
    or a dict (a raw trace, or a report side with ``task`` supplied).
    ``instructions`` (``{system_prompt, source}``) stands in for the
    trace's own when the caller knows them (a lineage's artifacts)."""
    try:
        view = RunView(run)
    except (ValueError, TypeError) as exc:
        return _empty_run(f"the run cannot be read: {exc}", run_name(run))
    info = _agent_of(run)
    task_block = _task_block(task if task is not None else info["task"])
    steps = view.steps
    if not steps:
        return _empty_run("the run has no steps", view.name, view.synthetic, task if task is not None else info["task"])
    declared = info["model"]
    instr = _instructions(info, instructions)
    declared_tools, used_tools = _tools(info, steps)
    harness = info["harness"] if isinstance(info["harness"], dict) else {}
    framework = str(harness.get("adapter") or "") or None
    models = _models(steps, declared)
    corpus = corpus_of(steps)
    answer, answer_source = _answer_text(steps, info)
    provenance = provenance_of(steps, answer, answer_source, corpus["sources"])
    chain = chain_of(steps, info, instr, declared, provenance)
    missing = []
    if not task_block["prompt"]:
        missing.append("no task prompt recorded")
    if not any(m["model"] for m in models):
        missing.append("no model recorded on the trace or its steps")
    if not any((st.input or "").strip() or (st.output or "").strip() for st in steps) and not answer.strip():
        missing.append("no step carries text and the answer is empty")
    payload = {
        "task": task_block,
        "agent": {"name": view.name, "model": declared, "version": info["version"], "framework": framework,
                  "instructions": instr, "tools_declared": declared_tools, "tools_used": used_tools},
        "models": models, "corpus": corpus, "provenance": provenance, "chain": chain, "synthetic": view.synthetic,
    }
    payload["narrative"] = _run_narrative(view.name, payload, missing)
    if missing:
        return unmeasurable("; ".join(missing), **payload)
    return measurable(payload)


def _run_narrative(name: str, p: dict, missing: list) -> str:
    t, a, c, pv = p["task"], p["agent"], p["corpus"], p["provenance"]
    parts = [f"{name} was given a {t['prompt_chars']}-character prompt"
             + (f" with a {t['expected_chars']}-character expected answer" if t["expected"] is not None else " and no expected answer")]
    ins = a["instructions"]
    parts.append(f"its instructions are recorded ({ins['chars']} characters, {ins['source']})" if ins["system_prompt"] is not None
                 else "no instructions are recorded on the trace")
    named = [m for m in p["models"] if m["model"]]
    if named:
        parts.append("its steps were produced by " + join_names(f"{m['model']} ({plural(m['steps'], 'step')}, {m['source']})" for m in named))
    else:
        parts.append("no model is recorded")
    parts.append(f"it read {plural(c['distinct'], 'distinct source')} in {plural(c['fetches'], 'fetch', 'fetches')}"
                 + (f" ({c['repeated_reads']} repeated)" if c["repeated_reads"] else "") + f", {num(c['total_chars'])} characters back")
    if pv["atoms"]:
        parts.append(f"its answer carries {plural(pv['atoms'], 'typed value')}, {pv['supported']} traced to a fetched output and "
                     f"{pv['unsupported']} not ({pct(pv['grounded_share'])} grounded)")
    else:
        parts.append("its answer carries no typed value to trace")
    text = "; ".join(parts) + "."
    if missing:
        text += " Unmeasurable: " + "; ".join(missing) + "."
    return text


# ---------------------------------------------------------------- the pair

def instructions_diff(a: Optional[str], b: Optional[str], from_id: str = "a", to_id: str = "b") -> dict:
    """The unified diff of two instruction texts, hunk by hunk; ``same``
    is None when either side has none."""
    if a is None or b is None:
        which = "either side" if a is None and b is None else (from_id if a is None else to_id)
        return {"same": None, "hunks": [], "added": None, "removed": None,
                "reason": f"no instructions recorded on {which}"}
    lines = list(difflib.unified_diff(a.splitlines(), b.splitlines(), fromfile=f"{from_id}/system_prompt",
                                      tofile=f"{to_id}/system_prompt", lineterm="", n=1))
    hunks: list = []
    for line in lines[2:]:
        if line.startswith("@@"):
            hunks.append([line])
        elif hunks:
            hunks[-1].append(line)
    body = lines[2:]
    return {"same": a == b, "hunks": ["\n".join(h) for h in hunks],
            "added": sum(1 for l in body if l.startswith("+")), "removed": sum(1 for l in body if l.startswith("-")),
            "reason": None}


def corpus_diff(a: dict, b: dict) -> dict:
    ids_a = [s["id"] for s in (a or {}).get("sources") or []]
    ids_b = [s["id"] for s in (b or {}).get("sources") or []]
    set_a, set_b = set(ids_a), set(ids_b)
    union = set_a | set_b
    return {"shared": [i for i in ids_a if i in set_b], "only_a": [i for i in ids_a if i not in set_b],
            "only_b": [i for i in ids_b if i not in set_a],
            "jaccard": rounded(len(set_a & set_b) / len(union)) if union else None,
            "basis": "sources identified by tool name and normalised input; Jaccard over the two sets of ids"}


def _prov_summary(d: dict) -> dict:
    pv = d.get("provenance") or {}
    return {"atoms": pv.get("atoms"), "supported": pv.get("supported"), "unsupported": pv.get("unsupported"),
            "grounded_share": pv.get("grounded_share"), "ungrounded_share": pv.get("ungrounded_share")}


def _readings(report: dict, side: str) -> dict:
    """The pair's own grounding readings for one side, verbatim, when it
    carries them: the reading's answer basis and the semantic grounding."""
    out: dict = {}
    reading = ((report.get("reading") or {}).get(side) or {}) if isinstance(report.get("reading"), dict) else {}
    basis = reading.get("answer_basis") if isinstance(reading, dict) else None
    if isinstance(basis, dict):
        out["answer_basis"] = {"atoms": basis.get("atoms"), "supported": basis.get("supported"), "status": basis.get("status"),
                               "source": "reading.answer_basis"}
    sem = report.get("semantic") if isinstance(report.get("semantic"), dict) else {}
    g = (sem.get("grounding") or {}).get(side) if isinstance(sem.get("grounding"), dict) else None
    if isinstance(g, dict):
        out["semantic"] = {"claims_total": g.get("claims_total"), "claims_grounded": g.get("claims_grounded"),
                           "score": g.get("score"), "source": "semantic.grounding"}
    return out


def data_pair(report: dict, a: Any = None, b: Any = None) -> dict:
    """Both runs' data sides, the prompt they shared, the diff of their
    instructions, the corpus diff, the models and the provenance."""
    task = report.get("task") if isinstance(report.get("task"), dict) else None
    da = data_run(a if a is not None else report.get("a") or {}, task=task)
    db = data_run(b if b is not None else report.get("b") or {}, task=task)
    name_a, name_b = run_name(report.get("a"), "A"), run_name(report.get("b"), "B")
    ins_a, ins_b = (da.get("agent") or {}).get("instructions") or {}, (db.get("agent") or {}).get("instructions") or {}
    idiff = instructions_diff(ins_a.get("system_prompt"), ins_b.get("system_prompt"), name_a, name_b)
    cdiff = corpus_diff(da.get("corpus") or {}, db.get("corpus") or {})
    # one name each: a model attributed by step telemetry and the same model
    # declared on the trace are two rows of the side's reading, one model
    models_a = list(dict.fromkeys(m["model"] for m in da.get("models") or [] if m["model"]))
    models_b = list(dict.fromkeys(m["model"] for m in db.get("models") or [] if m["model"]))
    models = {"a": models_a, "b": models_b, "same": (set(models_a) == set(models_b)) if models_a and models_b else None}
    pa, pb = _prov_summary(da), _prov_summary(db)
    delta = (rounded(pa["grounded_share"] - pb["grounded_share"])
             if pa["grounded_share"] is not None and pb["grounded_share"] is not None else None)
    provenance = {"a": pa, "b": pb, "delta_grounded": delta,
                  "readings": {"a": _readings(report, "a"), "b": _readings(report, "b")},
                  "basis": "delta_grounded = A's grounded share − B's, null when either answer carries no typed value"}
    payload = {"a": da, "b": db, "task": _task_block(task), "instructions_diff": idiff, "corpus_diff": cdiff,
               "models": models, "provenance": provenance}
    payload["narrative"] = _pair_narrative(name_a, name_b, payload)
    if not (da["measurable"] and db["measurable"]):
        return unmeasurable("at least one run cannot be read: " + "; ".join(
            f"{n}: {d['reason']}" for n, d in ((name_a, da), (name_b, db)) if not d["measurable"]), version=VERSION, **payload)
    return measurable(payload, version=VERSION)


def _pair_narrative(name_a: str, name_b: str, p: dict) -> str:
    t = p["task"]
    parts = [f"both agents were given the same {t['prompt_chars']}-character prompt"
             + (" with an expected answer" if t["expected"] is not None else " and no expected answer")]
    idiff = p["instructions_diff"]
    if idiff["same"] is None:
        parts.append(idiff["reason"])
    elif idiff["same"]:
        parts.append("the same instructions")
    else:
        parts.append(f"instructions that differ in {plural(len(idiff['hunks']), 'hunk')} (+{idiff['added']} −{idiff['removed']} lines)")
    m = p["models"]
    if m["a"] and m["b"]:
        parts.append(("the same model, " if m["same"] else "different models: ") + f"{name_a} {join_names(m['a'])}"
                     + ("" if m["same"] else f", {name_b} {join_names(m['b'])}"))
    c = p["corpus_diff"]
    ca, cb = (p["a"].get("corpus") or {}).get("distinct", 0), (p["b"].get("corpus") or {}).get("distinct", 0)
    parts.append(f"{name_a} read {plural(ca, 'source')} and {name_b} {cb}, {len(c['shared'])} shared"
                 + (f" (Jaccard {num(c['jaccard'])})" if c["jaccard"] is not None else ""))
    pv = p["provenance"]
    for name, side in ((name_a, pv["a"]), (name_b, pv["b"])):
        if side["atoms"]:
            parts.append(f"{name}'s answer rests on {side['supported']} of {plural(side['atoms'], 'typed value')} fetched")
        elif side["atoms"] == 0:
            parts.append(f"{name}'s answer carries no typed value")
    return "; ".join(parts) + "."


# ----------------------------------------------------------- the aggregate

def data_aggregate(trajectories: list) -> dict:
    """Per agent over every run: the models seen, the instructions'
    digest when every run records the same text, the distinct sources
    and the ones read in more than one run, the mean grounded share; per
    task the prompt length and whether an expected answer is recorded."""
    if not trajectories:
        return unmeasurable("no trajectories in the aggregate context", version=VERSION, agents={}, tasks={},
                            narrative="No run reached the aggregate, so nothing was read.")
    agents: dict = {}
    tasks: dict = {}
    for traj in sorted(trajectories, key=lambda t: (t.agent.name, t.task.id, t.run_id)):
        d = data_run(traj)
        ag = agents.setdefault(traj.agent.name, {"runs": 0, "measurable_runs": 0, "models": [], "instructions_digest": None,
                                                  "_prompts": set(), "sources_distinct": 0, "sources_shared_across_runs": 0,
                                                  "_sources": {}, "grounded_share_mean": None, "_grounded": [],
                                                  "atoms": 0, "supported": 0, "synthetic": False})
        ag["runs"] += 1
        ag["measurable_runs"] += 1 if d["measurable"] else 0
        for m in d.get("models") or []:
            if m["model"] and m["model"] not in ag["models"]:
                ag["models"].append(m["model"])
        ins = ((d.get("agent") or {}).get("instructions") or {}).get("system_prompt")
        ag["_prompts"].add(ins)
        for s in ((d.get("corpus") or {}).get("sources") or []):
            ag["_sources"][s["id"]] = ag["_sources"].get(s["id"], 0) + 1
        pv = d.get("provenance") or {}
        if pv.get("grounded_share") is not None:
            ag["_grounded"].append(pv["grounded_share"])
        ag["atoms"] += pv.get("atoms") or 0
        ag["supported"] += pv.get("supported") or 0
        ag["synthetic"] = ag["synthetic"] or bool(d.get("synthetic"))
        t = tasks.setdefault(traj.task.id, {"prompt_chars": len(traj.task.prompt or ""), "expected": traj.task.expected is not None})
        t["prompt_chars"] = max(t["prompt_chars"], len(traj.task.prompt or ""))
    for ag in agents.values():
        prompts = ag.pop("_prompts")
        texts = [p for p in prompts if isinstance(p, str)]
        ag["instructions_digest"] = _sha(texts[0]) if len(prompts) == 1 and texts else None
        ag["instructions_distinct"] = len(texts)
        srcs = ag.pop("_sources")
        ag["sources_distinct"] = len(srcs)
        ag["sources_shared_across_runs"] = sum(1 for n in srcs.values() if n > 1)
        grounded = ag.pop("_grounded")
        ag["grounded_share_mean"] = rounded(mean(grounded)) if grounded else None
        ag["grounded_runs"] = len(grounded)
        ag["models"] = sorted(ag["models"])
    payload = {"agents": dict(sorted(agents.items())), "tasks": dict(sorted(tasks.items()))}
    payload["narrative"] = _aggregate_narrative(payload)
    return measurable(payload, version=VERSION)


def _aggregate_narrative(p: dict) -> str:
    bits = []
    for name, ag in p["agents"].items():
        models = join_names(ag["models"]) if ag["models"] else "no recorded model"
        grounded = (f"a mean grounded share of {pct(ag['grounded_share_mean'])} over {plural(ag['grounded_runs'], 'run')} with typed values"
                    if ag["grounded_share_mean"] is not None else "no answer with a typed value to trace")
        bits.append(f"{name} ran {models} over {plural(ag['runs'], 'run')}, read {plural(ag['sources_distinct'], 'distinct source')} "
                    f"({ag['sources_shared_across_runs']} in more than one run), "
                    + ("instructions recorded and identical across runs" if ag["instructions_digest"] else
                       (f"{ag['instructions_distinct']} distinct instruction texts" if ag["instructions_distinct"] > 1 else "no instructions recorded"))
                    + f", {grounded}")
    expected = sum(1 for t in p["tasks"].values() if t["expected"])
    return "; ".join(bits) + f"; {plural(len(p['tasks']), 'task')}, {expected} with an expected answer." if bits else "No agent read."


# ------------------------------------------------------------- the lineage

def _artifacts_instructions(artifacts: Any) -> dict:
    art = artifacts if isinstance(artifacts, dict) else {}
    prompt = art.get("system_prompt") if isinstance(art.get("system_prompt"), str) else None
    return {"system_prompt": prompt, "chars": len(prompt) if prompt is not None else None,
            "rules": list(art.get("rules")) if isinstance(art.get("rules"), list) else None,
            "skills": [s.get("name") if isinstance(s, dict) else s for s in art["skills"]] if isinstance(art.get("skills"), list) else None,
            "tools": list(art.get("tools")) if isinstance(art.get("tools"), list) else None,
            "memory_n": len(art["memory"]) if isinstance(art.get("memory"), list) else None,
            "config": dict(art.get("config")) if isinstance(art.get("config"), dict) else None,
            "source": "lineage artifacts" if art else None}


def _episode_key(traj: Trajectory) -> str:
    return f"{traj.task.id}__{traj.agent.name}__{traj.run_id}"


def _gen_data(gen: dict) -> dict:
    """Every episode of a generation read once: ``{key: data_run}`` by
    filename stem and by trace id, with the artifacts' instructions."""
    art = (gen.get("agent") or {}).get("artifacts") if isinstance(gen.get("agent"), dict) else None
    ins = _artifacts_instructions(art)
    override = {"system_prompt": ins["system_prompt"], "source": "lineage artifacts"} if ins["system_prompt"] is not None else None
    out: dict = {}
    for traj in gen.get("trajectories") or []:
        d = data_run(traj, instructions=override)
        out[_episode_key(traj)] = d
        if traj.trace_id:
            out.setdefault(traj.trace_id, d)
    return out


def _behaviour(gen_data: dict, tool_calls: dict) -> dict:
    seen = set()
    sources: set = set()
    grounded: list = []
    for d in gen_data.values():
        if id(d) in seen:
            continue
        seen.add(id(d))
        for s in ((d.get("corpus") or {}).get("sources") or []):
            sources.add(s["id"])
        share = (d.get("provenance") or {}).get("grounded_share")
        if share is not None:
            grounded.append(share)
    return {"tools": dict(tool_calls or {}), "sources": len(sources),
            "grounded": rounded(mean(grounded)) if grounded else None, "grounded_runs": len(grounded)}


def data_evolution(lineage: dict, evolution: dict, coevolution: Optional[dict] = None) -> dict:
    """How the agent evolved, from the data its evidence episodes read to
    the eval's flags, one row per lineage step."""
    from .coevolve import _synthetic  # the lineage's SYNTHETIC rule, implemented once there
    from .evolve import artifact_digest
    synthetic = _synthetic(lineage or {})
    family = (lineage or {}).get("family") or (evolution or {}).get("family")
    if not evolution or not evolution.get("measurable"):
        return unmeasurable(f"the evolution section is unmeasurable: {(evolution or {}).get('reason') or 'absent'}",
                            version=VERSION, family=family, generations=[], steps=[], synthetic=synthetic,
                            narrative="The lineage cannot be read, so how the agent evolved from its data cannot be either.")
    gens = list((lineage or {}).get("generations") or [])
    if len(gens) < 2:
        return unmeasurable("fewer than two generations", version=VERSION, family=family, generations=[], steps=[],
                            synthetic=synthetic, narrative="One generation is not an evolution.")
    ev_gens = {g["id"]: g for g in evolution.get("generations") or []}
    ev_steps = {s["index"]: s for s in evolution.get("steps") or []}
    co_steps = {s["index"]: s for s in ((coevolution or {}).get("steps") or [])} if isinstance(coevolution, dict) else {}
    eval_after = {e.get("after_step"): e.get("id") for e in ((coevolution or {}).get("eval_generations") or [])} \
        if isinstance(coevolution, dict) else {}
    per_gen = [_gen_data(g) for g in gens]
    out_gens = []
    for g in gens:
        art = (g.get("agent") or {}).get("artifacts") if isinstance(g.get("agent"), dict) else None
        out_gens.append({"id": g["id"], "instructions": _artifacts_instructions(art), "digest": artifact_digest(art)})
    steps = []
    for i in range(1, len(gens)):
        a, b = gens[i - 1], gens[i]
        ev = ev_steps.get(i) or {}
        evidence = ev.get("evidence") if isinstance(ev.get("evidence"), dict) else \
            ((b.get("agent") or {}).get("evidence") if isinstance(b.get("agent"), dict) else None)
        episodes = [e for e in ((evidence or {}).get("episodes") or []) if isinstance(e, str)] if isinstance(evidence, dict) else []
        check = ev.get("evidence_check") or {}
        data_rows = []
        for ep in episodes:
            key = ep[:-5] if ep.endswith(".json") else ep
            d = per_gen[i - 1].get(key) or per_gen[i - 1].get(ep)
            if d is None:
                data_rows.append({"episode": ep, "found": False, "sources": [], "grounded_share": None, "success": None})
                continue
            data_rows.append({"episode": ep, "found": True,
                              "sources": [s["id"] for s in ((d.get("corpus") or {}).get("sources") or [])],
                              "fetches": (d.get("corpus") or {}).get("fetches"),
                              "grounded_share": (d.get("provenance") or {}).get("grounded_share"),
                              "success": a.get("outcomes", {}).get(key)})
        diff = ev.get("diff") or {}
        sp = diff.get("system_prompt") if isinstance(diff.get("system_prompt"), dict) else {}
        rules = diff.get("rules") if isinstance(diff.get("rules"), dict) else {}
        config = diff.get("config") if isinstance(diff.get("config"), dict) else {}
        change = {"summary": diff.get("summary"), "hunks": list(sp.get("hunks") or []),
                  "prompt_added": sp.get("added"), "prompt_removed": sp.get("removed"),
                  "rules_added": list(rules.get("added") or []), "rules_removed": list(rules.get("removed") or []),
                  "config_changed": list(config.get("changed") or []), "protected_touched": list(diff.get("protected_touched") or []),
                  "source": "evolution.steps[].diff"}
        before = _behaviour(per_gen[i - 1], (ev_gens.get(a["id"]) or {}).get("tool_calls") or {})
        after = _behaviour(per_gen[i], (ev_gens.get(b["id"]) or {}).get("tool_calls") or {})
        behaviour = {"tools_before": before["tools"], "tools_after": after["tools"],
                     "sources_before": before["sources"], "sources_after": after["sources"],
                     "grounded_before": before["grounded"], "grounded_after": after["grounded"],
                     "grounded_runs": {"before": before["grounded_runs"], "after": after["grounded_runs"]},
                     "basis": "tool calls summed over the generation's episodes (evolution.generations[].tool_calls); distinct "
                              "sources over its episodes; grounded = mean of the episodes' grounded shares where an answer carries a typed value"}
        effect_src = ev.get("effect") or {}
        effect = {"verdict": ev.get("verdict"), "flags": list(ev.get("flags") or []),
                  "improvement": _band(effect_src.get("improvement")), "improvement_success": _band(effect_src.get("improvement_success")),
                  "source": "evolution.steps[]"}
        co = co_steps.get(i) or {}
        evolved = co.get("evolved") if isinstance(co.get("evolved"), dict) else {}
        label = f"{a['id']}→{b['id']}"
        eval_block = {"flags": [{"metric": f.get("metric"), "delta": f.get("delta"), "direction": f.get("direction"),
                                 "learned": bool(f.get("learned"))} for f in (evolved.get("flags") or []) if isinstance(f, dict)],
                      "learned": sorted({f.get("metric") for f in (evolved.get("flags") or []) if isinstance(f, dict) and f.get("learned")}),
                      "eval_gen": eval_after.get(label), "reading": evolved.get("reading"),
                      "source": "coevolution.steps[].evolved" if co else None}
        row = {"from": a["id"], "to": b["id"], "index": i,
               "evidence": {"episodes": episodes, "found": check.get("found"), "failures": check.get("failures"), "data": data_rows,
                            "summary": (evidence or {}).get("summary") if isinstance(evidence, dict) else None},
               "change": change, "behaviour": behaviour, "effect": effect, "eval": eval_block}
        row["reading"] = _step_reading(row)
        steps.append(row)
    payload = {"family": family, "generations": out_gens, "steps": steps, "synthetic": synthetic}
    payload["narrative"] = _lineage_narrative(payload)
    return measurable(payload, version=VERSION)


def _band(block: Any) -> dict:
    block = block if isinstance(block, dict) else {}
    return {"point": block.get("point"), "lo": block.get("lo"), "hi": block.get("hi")}


def _tools_delta(before: dict, after: dict) -> str:
    names = sorted(set(before) | set(after))
    moved = [(n, after.get(n, 0) - before.get(n, 0)) for n in names if after.get(n, 0) != before.get(n, 0)]
    if not moved:
        return "the same tool counts"
    return join_names(f"{n} {'+' if d > 0 else '−'}{abs(d)}" for n, d in moved)


def _step_reading(row: dict) -> str:
    ev, ch, bh, ef, ex = row["evidence"], row["change"], row["behaviour"], row["effect"], row["eval"]
    found = [d for d in ev["data"] if d["found"]]
    if ev["episodes"]:
        srcs = sorted({s for d in found for s in d["sources"]})
        shares = [d["grounded_share"] for d in found if d["grounded_share"] is not None]
        first = (f"the {plural(len(ev['episodes']), 'episode')} cited ({ev['found']} found, {ev['failures']} failures) read "
                 f"{plural(len(srcs), 'distinct source')}"
                 + (f" with a mean grounded share of {pct(mean(shares))}" if shares else ", no answer among them carrying a typed value"))
    else:
        first = "the step cites no episode"
    change = ch["summary"] or "no artifact diff"
    if ch["hunks"]:
        change += f" ({plural(len(ch['hunks']), 'prompt hunk')})"
    behaviour = (f"its behaviour moved: {_tools_delta(bh['tools_before'], bh['tools_after'])}; sources {bh['sources_before']} → "
                 f"{bh['sources_after']}; grounded {pct(bh['grounded_before'])} → {pct(bh['grounded_after'])}")
    base = (f"the base eval said {ef['verdict'] or 'nothing'} ({', '.join(ef['flags'])})" if ef["flags"]
            else f"the base eval said {ef['verdict'] or 'nothing'}")
    if ex["source"] is None:
        evolved = "no evolved eval walked the step"
    elif ex["flags"]:
        evolved = "the evolved eval flags " + join_names(f"{f['metric']} ({num(f['delta'].get('point') if isinstance(f['delta'], dict) else None)})"
                                                         for f in ex["flags"])
    else:
        evolved = "the evolved eval flags nothing"
    if ex["eval_gen"]:
        evolved += f"; it advanced to {ex['eval_gen']} after this step"
    return f"{first}; the agent changed: {change}; {behaviour}; {base}; {evolved}."


def _lineage_narrative(p: dict) -> str:
    if not p["steps"]:
        return f"{p['family'] or 'the lineage'}: no step to read."
    bits = []
    for s in p["steps"]:
        ex = s["eval"]
        bits.append(f"{s['from']}→{s['to']}: {plural(len(s['evidence']['episodes']), 'episode')} cited, "
                    f"{plural(len(s['change']['hunks']), 'prompt hunk')}, sources {s['behaviour']['sources_before']} → {s['behaviour']['sources_after']}, "
                    f"grounded {pct(s['behaviour']['grounded_before'])} → {pct(s['behaviour']['grounded_after'])}, "
                    f"{s['effect']['verdict'] or 'unread'}" + (f", {len(ex['flags'])} evolved flag(s)" if ex["flags"] else ""))
    return f"{p['family'] or 'the lineage'} over {plural(len(p['steps']), 'step')} — " + "; ".join(bits) + "."


# ------------------------------------------------------------ registration

@_sections.register("pair", "data", after=("reading", "semantic", "fetches"))
def _pair_section(report: dict, ctx: "_sections.PairContext") -> dict:
    return data_pair(report, getattr(ctx, "a", None), getattr(ctx, "b", None))


@_sections.register("aggregate", "data", after=("fetches",))
def _aggregate_section(agg: dict, ctx: "_sections.AggregateContext") -> dict:
    return data_aggregate(list(getattr(ctx, "trajectories", None) or []))


@_sections.register("lineage", "data_evolution", requires=("evolution",), after=("coevolution", "evolution_compare"))
def _lineage_section(agg: dict, ctx: "_sections.LineageContext") -> dict:
    return data_evolution(getattr(ctx, "lineage", None) or {}, agg.get("evolution") or {}, agg.get("coevolution"))


__all__ = ["VERSION", "QUERY_CHARS", "CHAIN_OVERLAP", "TEXT_CAP", "TOKEN_MIN", "MODEL_KINDS", "tokens_of", "containment",
           "source_id", "corpus_of", "provenance_of", "chain_of", "data_run", "instructions_diff", "corpus_diff", "data_pair",
           "data_aggregate", "data_evolution"]
