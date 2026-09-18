"""The bundle: one or more output directories packed into one
self-contained, content-addressed directory with a three-level index, a
page and a key.

A member is what ``batch``, ``runs``, ``evolve``, ``coevolve`` or
``fleet`` wrote — ``aggregate.json`` and the ``report_*.json`` beside it,
or a ``fleet.json`` — read through the same loader the Grafana export
uses. The bundle's id is the SHA-256 of the canonical JSON (sorted keys,
no whitespace) of every member's aggregate and reports concatenated in
member order, so the same outputs give the same id on any machine, and
nothing in the bundle carries a timestamp: the same inputs give the same
bytes.

Three levels of grain are indexed from the members:

* **overview** (level 1) — what agents ran, with runs, tasks, a Wilson
  interval on their successes, tokens, cost, seconds and fetches summed
  over the runs that recorded them (and how many did); which are
  self-evolving, their lineages and the eval loops; the totals; the
  sections present; a reading.
* **runs** (level 2) — one row per run across members, keyed
  ``<member>/<task>/<agent>/<run>``, each number taken from the source
  that recorded it (the scorecard's per-run rows, the budget and fetches
  ledgers, a lineage's episodes, a report's steps) and ``null`` where none
  did — never 0 for unrecorded.
* **run** (level 3) — one record per run under ``runs/``: the steps
  (with their text, capped at ``TEXT_CAP`` characters and flagged when
  cut; :meth:`Bundle.step` returns the whole of one), the budget,
  fetches and data readings, and the timeline in the shape the
  Evolution timescape draws. A run whose steps are not in the output (a
  runs layout keeps one representative pair per task; a lineage keeps
  the last pair's reports) has a record that says so — unless the
  source traces are given (``--traces DIR …``): then every such record
  is completed from the trace file matched by ``trace_id`` (else by
  task, agent and run id), the file is copied under ``traces/<member>/…``
  so the bundle stays self-contained, and the record says
  ``steps_source: "trace <path relative to the bundle>"``. Without
  traces nothing changes.

A second digest, ``records_digest``, covers what the id does not: the
level-3 records under ``runs/`` and the copied traces; :func:`verify`
recomputes both and reports ``match`` and ``records_match`` separately,
so a tampered run record is noticed even though the id — the members'
content — still matches.

The key (``agentdiff1:…``) is the level-1 overview compressed into one
paste-safe line that also names the bundle by its id: a tool holding the
bundle can verify and open it; one that does not still knows what ran.
Every number here is a count or a sum over recorded steps carried through
from the members; nothing is estimated on the way in, no model
identifier is copied, and SYNTHETIC is carried through per run.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import shutil
import zlib
from pathlib import Path
from typing import Any, Optional, Union

from . import sections as _sections
from ._stats import finite, rounded
from ._text import join_names, num, pct, plural
from .budget import budget_aggregate, budget_run
from .data import TEXT_CAP, data_run
from .evolve import _flags as timeline_flags, _kind as timeline_kind
from .fetches import FETCH_KINDS, fetches_aggregate, fetches_run, synthetic_of
from .grafana import load_target
from .report import render_html
from .section import measurable, unmeasurable
from .statistics import wilson_interval
from .trace import Trajectory

VERSION = 1
KEY_PREFIX = "agentdiff1:"
KEY_VERSION = 1
#: the longest key: past it, agents are dropped from the key (never from the bundle)
KEY_MAX_BYTES = 2000
#: how many agents a key names before it says how many more there were
KEY_AGENTS = 12
#: the most a key's payload may decompress to; a real payload is a few kilobytes
KEY_DECODED_MAX = 64 * 1024
#: how many runs the overview names as heaviest
HEAVIEST = 8
#: the keys of a pair report that are not sections
BASE_REPORT_KEYS = ("task", "a", "b", "alignment", "divergences", "attribution", "answer_eval", "metrics_delta")
#: the level-2 sort keys and the row field each reads
SORT_FIELDS = {"tokens": "tokens", "cost": "cost_usd", "seconds": "seconds", "fetches": "fetches", "steps": "steps",
               "errors": "errors"}
RESOURCE_ROOT = "agentdiff://bundle/"

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


# ----------------------------------------------------------------- hashing

def canonical(obj: Any) -> bytes:
    """The canonical JSON of ``obj``: sorted keys, no whitespace, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(members: list) -> str:
    """``sha256:<hex>`` over every member's aggregate and reports (a
    fleet's ranking first) in member order."""
    h = hashlib.sha256()
    for m in members:
        if m.get("fleet") is not None:
            h.update(canonical(m["fleet"]))
        h.update(canonical(m["aggregate"]))
        for report in m["reports"]:
            h.update(canonical(report))
    return "sha256:" + h.hexdigest()


def records_digest(records: dict, traces: dict) -> str:
    """``sha256:<hex>`` over the level-3 records (the canonical JSON of
    ``{key: record}``) and the bytes of the copied traces (``{path in the
    bundle: bytes}``, in path order) — what the id does not cover."""
    h = hashlib.sha256()
    h.update(canonical(records))
    for rel in sorted(traces):
        h.update(rel.encode("utf-8") + b"\n")
        h.update(traces[rel])
    return "sha256:" + h.hexdigest()


# ----------------------------------------------------------------- members

def _kind_of(target: dict) -> str:
    if target["kind"] == "fleet":
        return "fleet"
    agg = target["aggregate"]
    if "coevolution" in agg:
        return "coevolve"
    if "evolution" in agg:
        return "evolve"
    if "stability" in agg or "reliability" in agg:
        return "runs"
    return "batch"


def _agents_of(aggregate: dict, reports: list) -> list:
    names = set()
    for report in reports:
        for side in ("a", "b"):
            name = ((report.get(side) or {}).get("agent") or {}).get("name")
            if name:
                names.add(str(name))
    for row in ((aggregate.get("scorecard") or {}).get("per_run") or []):
        if row.get("agent"):
            names.add(str(row["agent"]))
    for gen in ((aggregate.get("evolution") or {}).get("generations") or []):
        if gen.get("policy"):
            names.add(str(gen["policy"]))
    return sorted(names)


def _sections_of(aggregate: dict, reports: list) -> list:
    known = set()
    for scope in _sections.SCOPES:
        known.update(_sections.registered(scope))
    present = {k for k in aggregate if k in known}
    for report in reports:
        present.update(k for k in report if k in known and k not in BASE_REPORT_KEYS)
    return sorted(present)


def read_member(path: Union[str, Path]) -> dict:
    """One output directory as a member: its kind (``batch``, ``runs``,
    ``evolve``, ``coevolve``, ``fleet``), source name, aggregate, reports,
    fleet payload when it is one, tasks, agents, lineage family and the
    section keys present. ``ValueError`` when the path is not an output
    directory."""
    target = load_target(path)
    if target["kind"] == "trace":
        raise ValueError(f"{path} is a trace; give the output directory a command wrote")
    aggregate, reports = target["aggregate"], target["reports"]
    evolution = aggregate.get("evolution") or {}
    return {"kind": _kind_of(target), "source": Path(path).name or str(path), "path": str(Path(path)),
            "aggregate": aggregate, "reports": reports, "fleet": target.get("fleet"),
            "tasks": sorted({str((r.get("task") or {}).get("id")) for r in reports if (r.get("task") or {}).get("id")}
                            | {str(row["task"]) for row in ((aggregate.get("scorecard") or {}).get("per_run") or []) if row.get("task")}),
            "agents": _agents_of(aggregate, reports),
            "lineage": str(evolution["family"]) if evolution.get("family") else None,
            "sections": _sections_of(aggregate, reports)}


def _labels(members: list) -> list:
    """One label per member: the source name, made distinct by index when
    two members share it."""
    seen: dict = {}
    labels = []
    for i, m in enumerate(members):
        name = m["source"]
        if name in seen or name in labels:
            name = f"{name}-{i}"
        seen[name] = i
        labels.append(name)
    return labels


# ------------------------------------------------------------- the rows

def _blank_row(label: str, task: str, agent: str, run: str) -> dict:
    return {"key": f"{label}/{task}/{agent}/{run}", "member": label, "task": task, "agent": agent, "run_id": run,
            "success": None, "steps": None, "tool_calls": None, "tools": {}, "tokens": None,
            "tokens_measured_share": None, "cost_usd": None, "seconds": None, "fetches": None, "errors": None,
            "repeats": None, "retries": None, "return": None, "lineage_gen": None, "synthetic": False, "detail": False, "basis": []}


def _set(row: dict, source: str, **fields: Any) -> None:
    for k, v in fields.items():
        if v is not None:
            row[k] = v
    if source not in row["basis"]:
        row["basis"].append(source)


def _fill(row: dict, source: str, **fields: Any) -> None:
    """Like :func:`_set`, but a number a source already recorded is kept:
    only ``None`` (and an empty ``tools``) is filled."""
    for k, v in fields.items():
        if v is not None and (row.get(k) is None or (k == "tools" and not row.get(k))):
            row[k] = v
    if source not in row["basis"]:
        row["basis"].append(source)


def _traj_of_side(report: dict, side: str) -> Optional[Trajectory]:
    """A typed trajectory from a report side, the task riding along; None
    when the side cannot be read as one."""
    block = report.get(side)
    if not isinstance(block, dict):
        return None
    try:
        t = Trajectory.from_dict({**block, "task": report.get("task") or {}})
    except ValueError:
        return None
    if isinstance(block.get("harness"), dict):
        t.harness = block["harness"]  # type: ignore[attr-defined]
    return t


def _rewards_of(report: dict, side: str) -> tuple:
    """``({index: reward}, basis)`` from the report's ``rl`` reading —
    recorded, else shaped from the labels, as that section says — or from
    the steps' own recorded rewards."""
    rl = report.get("rl") or {}
    block = rl.get(side) if isinstance(rl, dict) else None
    if isinstance(block, dict) and block.get("measurable") and isinstance(block.get("rewards"), list):
        return ({r["step"]: r.get("reward") for r in block["rewards"] if isinstance(r, dict) and isinstance(r.get("step"), int)},
                f"rl section, {rl.get('source') or 'recorded'}")
    return {}, "steps as recorded (null where no reward was recorded)"


def _timeline(report: dict, side: str, traj: Trajectory) -> tuple:
    """The Evolution timescape's ``[[t0, dur, kind, name, reward, flags]]``
    for one report side, with the flags a pair establishes (decisive,
    fault) when the report can say."""
    from .impact import step_facts
    rewards, basis = _rewards_of(report, side)
    timing = {r.get("index"): r for r in (((report.get("timing") or {}).get(side) or {}).get("steps") or []) if isinstance(r, dict)}
    try:
        marks = {f["index"]: f for f in step_facts(report, side, [s.to_dict() for s in traj.steps])}
    except (KeyError, TypeError, ValueError, AttributeError):
        marks = {}
    out = []
    clock = 0.0
    for st in traj.steps:
        lat = float(st.latency_s) if finite(st.latency_s) and st.latency_s >= 0 else 0.0
        span_agent = (st.span or {}).get("agent") if isinstance(st.span, dict) else None
        mark = marks.get(st.index) or {}
        flags = {"error": bool(st.error), "wasted": bool((timing.get(st.index) or {}).get("wasted")),
                 "decisive": bool(mark.get("decisive")), "fault": bool(mark.get("fault")),
                 "verifier": isinstance(span_agent, str) and span_agent.lower().startswith("verif")}
        reward = rewards.get(st.index, st.reward if finite(st.reward) else None)
        out.append([round(clock, 4), round(lat, 4), timeline_kind(st.type), st.name or st.type or "", reward, timeline_flags(flags)])
        clock += lat
    return out, basis


def _capped(text: str) -> tuple:
    """``(text, truncated)``: the first ``TEXT_CAP`` characters and whether
    any were dropped; the full length rides in ``*_chars`` beside it."""
    text = text or ""
    if len(text) <= TEXT_CAP:
        return text, False
    return text[:TEXT_CAP], True


def _step_rows(traj: Trajectory) -> list:
    rows = []
    for st in traj.steps:
        input_text, input_truncated = _capped(st.input)
        output_text, output_truncated = _capped(st.output)
        rows.append({"index": st.index, "type": st.type, "name": st.name or "", "tokens": st.tokens,
                     "tokens_basis": st.tokens_basis, "latency_s": st.latency_s, "error": st.error, "effect": st.effect,
                     "reward": st.reward, "value": st.value, "input_chars": len(st.input or ""),
                     "output_chars": len(st.output or ""), "span": (st.span or {}).get("agent") if isinstance(st.span, dict) else None,
                     "input_text": input_text, "input_truncated": input_truncated,
                     "output_text": output_text, "output_truncated": output_truncated})
    return rows


def _member_rows(m: dict, label: str) -> tuple:
    """``(rows, details, ids)`` for one member: the level-2 rows keyed by
    (task, agent, run), for runs whose steps a report carries the
    level-3 detail, and the trace id each row's source named (a lineage's
    episodes, the scorecard), by the same key, for matching a trace."""
    rows: dict = {}
    details: dict = {}
    ids: dict = {}
    agg = m["aggregate"]

    def row(task: str, agent: str, run: str) -> dict:
        return rows.setdefault((task, agent, run), _blank_row(label, task, agent, run))

    # a lineage's episodes: every generation, steps and seconds but no tokens;
    # SYNTHETIC from the generation's note or the lineage's eval, since an
    # episode entry carries no harness block of its own
    lineage_synthetic = bool((agg.get("coevolution") or {}).get("synthetic"))
    for gen in ((agg.get("evolution") or {}).get("generations") or []):
        agent = str(gen.get("policy") or "")
        if not agent:
            continue
        gen_synthetic = lineage_synthetic or str(gen.get("note") or "").upper().startswith("SYNTHETIC")
        for ep in gen.get("episodes") or []:
            if not isinstance(ep, dict) or not ep.get("task_id"):
                continue
            r = row(str(ep["task_id"]), agent, str(ep.get("run_id") or "r1"))
            if ep.get("trace_id"):
                ids[(str(ep["task_id"]), agent, str(ep.get("run_id") or "r1"))] = str(ep["trace_id"])
            tools = ep.get("tools") if isinstance(ep.get("tools"), dict) else None
            _set(r, "evolution", success=ep.get("success") if isinstance(ep.get("success"), bool) else None,
                 steps=ep.get("steps"), seconds=ep.get("seconds"), tools=tools, errors=ep.get("errors"),
                 fetches=sum(tools.values()) if tools else None, **{"return": ep.get("return")},
                 lineage_gen=str(gen.get("id")), synthetic=gen_synthetic)
            if isinstance(ep.get("timeline"), list):
                details.setdefault((str(ep["task_id"]), agent, str(ep.get("run_id") or "r1")), {})["timeline"] = ep["timeline"]
    # the scorecard: one row per run, spend and tool counts
    for sc in ((agg.get("scorecard") or {}).get("per_run") or []):
        if not sc.get("task") or not sc.get("agent"):
            continue
        r = row(str(sc["task"]), str(sc["agent"]), str(sc.get("run_id") or "r1"))
        if sc.get("trace_id"):
            ids.setdefault((str(sc["task"]), str(sc["agent"]), str(sc.get("run_id") or "r1")), str(sc["trace_id"]))
        spend, tools, traj = sc.get("spend") or {}, sc.get("tools") or {}, sc.get("trajectory") or {}
        cost = spend.get("cost_usd")
        _set(r, "scorecard", success=sc.get("success") if isinstance(sc.get("success"), bool) else None,
             steps=spend.get("steps"), tokens=spend.get("tokens"), cost_usd=cost if finite(cost) and cost > 0 else None,
             seconds=spend.get("latency_s"), tool_calls=tools.get("calls"), errors=tools.get("errors"),
             repeats=traj.get("repeated_calls"))
    # the budget and fetches ledgers: the labelled token shares and the fetch counts
    for br in ((agg.get("budget") or {}).get("runs") or []):
        r = row(str(br["task"]), str(br["agent"]), str(br["run"]))
        _set(r, "budget", tokens=br.get("tokens"), steps=br.get("steps"), cost_usd=br.get("cost_usd"), seconds=br.get("seconds"),
             tokens_measured_share=rounded(br["measured"] / br["tokens"]) if br.get("tokens") else None,
             synthetic=bool(br.get("synthetic")))
    for fr in ((agg.get("fetches") or {}).get("runs") or []):
        r = row(str(fr["task"]), str(fr["agent"]), str(fr["run"]))
        _set(r, "fetches", fetches=fr.get("fetches"), errors=fr.get("errors"), repeats=fr.get("repeats"),
             retries=fr.get("retries"),
             tools=fr.get("by_tool") if isinstance(fr.get("by_tool"), dict) else None, synthetic=bool(fr.get("synthetic")))
    # the reports: the steps themselves, so the whole third level
    for report in m["reports"]:
        task = str((report.get("task") or {}).get("id") or "")
        for side in ("a", "b"):
            traj = _traj_of_side(report, side)
            if traj is None or not task:
                continue
            r = row(task, traj.agent.name, traj.run_id)
            section = report.get("budget") or {}
            b = section.get(side) if isinstance(section, dict) and (section.get(side) or {}).get("measurable") else budget_run(traj)
            section = report.get("fetches") or {}
            f = section.get(side) if isinstance(section, dict) and (section.get(side) or {}).get("measurable") else fetches_run(traj)
            # the data side: the report's reading when it carries one (measurable or not — an
            # unmeasurable reading names what the trace lacks), else read from the side
            section = report.get("data") or {}
            d = section.get(side) if isinstance(section, dict) and isinstance(section.get(side), dict) \
                and "measurable" in section[side] else data_run(traj, task=report.get("task"))
            seconds = b["per_second"]["seconds"] if b["per_second"]["measurable"] else None
            tools: dict = {}
            for st in traj.steps:
                if st.type in FETCH_KINDS:
                    tools[st.name or "?"] = tools.get(st.name or "?", 0) + 1
            timeline, reward_basis = _timeline(report, side, traj)
            _set(r, "report", success=traj.outcome.success, steps=len(traj.steps),
                 tool_calls=sum(1 for st in traj.steps if st.type == "tool_call"), tools=dict(sorted(tools.items())),
                 tokens=b["tokens"]["total"], tokens_measured_share=rounded(b["tokens"]["measured"] / b["tokens"]["total"]) if b["tokens"]["total"] else None,
                 cost_usd=b["cost_usd"]["value"], seconds=seconds, fetches=f["counts"]["total"], errors=f["counts"]["errors"],
                 repeats=f["counts"]["repeats"], retries=f["counts"]["retries"],
                 synthetic=synthetic_of(getattr(traj, "harness", None)), detail=True)
            details[(task, traj.agent.name, traj.run_id)] = {
                "steps": _step_rows(traj), "budget": b, "fetches": f, "data": d, "timeline": timeline, "reward_basis": reward_basis,
                "trace_id": traj.trace_id, "trace_path": None, "report": f"report_{_UNSAFE.sub('_', task)}.json", "side": side}
    return rows, details, ids


# ------------------------------------------------------------ the traces

def load_traces(dirs: Any) -> tuple:
    """Every trace file under ``dirs`` (each walked recursively, files in
    sorted order): ``([{"root", "rel", "path", "trajectory"}], notes)``.
    The run id is the runs layout's ``<task>__<agent>__<run>`` name when
    the file has one, else what the trace carries; the ``harness`` block
    rides beside the typed trajectory. A JSON file that is not a trace
    is passed over; one that is a trace but invalid is counted in the
    notes with its reason, never guessed at."""
    entries: list = []
    notes: list = []
    for d in dirs or []:
        root = Path(d)
        if not root.is_dir():
            raise ValueError(f"--traces {d}: not a directory")
        for path in sorted(root.rglob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                notes.append(f"{path}: not valid JSON: {exc}")
                continue
            if not isinstance(data, dict) or "steps" not in data or "task" not in data:
                continue
            try:
                t = Trajectory.from_dict(data)
            except (ValueError, TypeError, KeyError) as exc:
                notes.append(f"{path}: {exc}")
                continue
            parts = path.stem.split("__")
            if len(parts) >= 3:
                t.run_id = parts[2]
            if isinstance(data.get("harness"), dict):
                t.harness = data["harness"]  # type: ignore[attr-defined]
            entries.append({"root": root, "rel": path.relative_to(root).as_posix(), "path": path, "trajectory": t})
    return entries, notes


class _TracePool:
    """The loaded traces indexed by trace id and by (task, agent, run),
    so a record is completed by its trace id first and by its key when
    the trace id is unknown or unmatched."""

    def __init__(self, entries: list) -> None:
        self.by_id: dict = {}
        self.by_key: dict = {}
        for e in entries:
            t = e["trajectory"]
            if t.trace_id:
                self.by_id.setdefault(t.trace_id, e)
            self.by_key.setdefault((t.task.id, t.agent.name, t.run_id), e)

    def match(self, trace_id: Optional[str], key: tuple) -> Optional[dict]:
        """The trace for a run: by trace id when the trace names the same
        task and agent (an id that names another agent's run is not a
        match, whatever it says), else by (task, agent, run id)."""
        e = self.by_id.get(trace_id) if trace_id else None
        if e is not None and (e["trajectory"].task.id, e["trajectory"].agent.name) == key[:2]:
            return e
        return self.by_key.get(key)


def _trace_file(label: str, rel: str, taken: dict, source: Path) -> str:
    """The bundle-relative path a trace is copied to: ``traces/<member>/<rel>``,
    suffixed ``~n`` before the extension when the same relative name
    arrives from another directory."""
    base = f"traces/{_UNSAFE.sub('_', label)}/{rel}"
    candidate = base
    n = 1
    while candidate in taken and taken[candidate] != source:
        n += 1
        stem, dot, ext = base.rpartition(".")
        candidate = f"{stem}~{n}.{ext}" if dot else f"{base}~{n}"
    taken[candidate] = source
    return candidate


def _complete(row: dict, detail: Optional[dict], entry: dict, rel: str) -> dict:
    """The level-3 detail of a run from its trace: the steps, the budget,
    fetches and data readings, the timeline (the lineage's episode
    timeline when the member carries one, since it holds the flags a
    pair established; else the trace's steps as recorded) and
    ``steps_source``. The row is filled where no source had recorded the
    number, never overwritten."""
    traj = entry["trajectory"]
    b, f, d = budget_run(traj), fetches_run(traj), data_run(traj)
    seconds = b["per_second"]["seconds"] if b["per_second"]["measurable"] else None
    tools: dict = {}
    for st in traj.steps:
        if st.type in FETCH_KINDS:
            tools[st.name or "?"] = tools.get(st.name or "?", 0) + 1
    if detail and detail.get("timeline"):
        timeline, reward_basis = detail["timeline"], "the lineage's episode timeline"
    else:
        timeline, reward_basis = _timeline({}, "a", traj)
    _fill(row, "trace", success=traj.outcome.success, steps=len(traj.steps),
          tool_calls=sum(1 for st in traj.steps if st.type == "tool_call"), tools=dict(sorted(tools.items())),
          tokens=b["tokens"]["total"], tokens_measured_share=rounded(b["tokens"]["measured"] / b["tokens"]["total"]) if b["tokens"]["total"] else None,
          cost_usd=b["cost_usd"]["value"], seconds=seconds, fetches=f["counts"]["total"], errors=f["counts"]["errors"],
          repeats=f["counts"]["repeats"], retries=f["counts"]["retries"])
    row["synthetic"] = bool(row["synthetic"] or synthetic_of(getattr(traj, "harness", None)))
    row["detail"] = True
    return {"steps": _step_rows(traj), "budget": b, "fetches": f, "data": d, "timeline": timeline, "reward_basis": reward_basis,
            "trace_id": traj.trace_id, "trace_path": rel, "report": None, "side": None, "steps_source": f"trace {rel}"}


def _record(row: dict, detail: Optional[dict]) -> dict:
    """The level-3 record of one run: the row (its step and fetch counts
    renamed ``steps_n`` and ``fetches_n``, since ``steps`` here is the list
    and ``fetches`` the reading), then the steps, the budget and fetches
    readings, the timeline and where it came from."""
    renamed = {"steps": "steps_n", "fetches": "fetches_n"}
    head = {renamed.get(k, k): v for k, v in row.items()}
    if detail and detail.get("steps"):
        source = {"steps_source": detail["steps_source"]} if detail.get("steps_source") else {}
        return measurable(dict(head, steps=detail["steps"], budget=detail["budget"], fetches=detail["fetches"], data=detail["data"],
                               timeline=detail["timeline"], reward_basis=detail["reward_basis"], trace_id=detail["trace_id"],
                               trace_path=detail["trace_path"], report=detail["report"], side=detail["side"], **source), version=VERSION)
    reason = ("the run's steps are not in the output: a runs layout keeps one representative pair per task, "
              "and a lineage keeps its episodes' timelines")
    return unmeasurable(reason, version=VERSION, **head, steps=[],
                        budget=unmeasurable(reason, tokens=None), fetches=unmeasurable(reason, records=[]),
                        data=unmeasurable(reason, task=None, agent=None, models=[], corpus=None, provenance=None, chain=None),
                        timeline=(detail or {}).get("timeline") or [], reward_basis=("the lineage's episode timeline" if detail else None),
                        trace_id=None, trace_path=None, report=None, side=None)


def _run_file(key: str, taken: set) -> str:
    name = "__".join(_UNSAFE.sub("_", part) for part in key.split("/"))
    candidate = name
    n = 1
    while candidate in taken:
        n += 1
        candidate = f"{name}~{n}"
    taken.add(candidate)
    return f"runs/{candidate}.json"


# ------------------------------------------------------------ the levels

def _sum_known(rows: list, field: str) -> tuple:
    values = [r[field] for r in rows if finite(r.get(field))]
    return (sum(values) if values else None), len(values)


def _success(rows: list) -> dict:
    known = [r["success"] for r in rows if isinstance(r.get("success"), bool)]
    if not known:
        return {"rate": None, "lo": None, "hi": None, "n": 0, "basis": "no run recorded an outcome"}
    k = sum(1 for s in known if s)
    lo, hi = wilson_interval(k, len(known))
    return {"rate": rounded(k / len(known)), "lo": lo, "hi": hi, "n": len(known),
            "basis": "successes over the runs recorded; a 95% Wilson interval, not a population claim"}


def _framework_of(m: dict, agent: str) -> Optional[str]:
    for report in m["reports"]:
        for side in ("a", "b"):
            block = report.get(side) or {}
            if str(((block.get("agent") or {}).get("name")) or "") == agent and isinstance(block.get("harness"), dict):
                adapter = block["harness"].get("adapter")
                return str(adapter) if adapter else None
    return None


def _lineages(members: list, labels: list) -> tuple:
    lineages, loops = [], []
    for m, label in zip(members, labels):
        ev = m["aggregate"].get("evolution") or {}
        if not ev.get("family"):
            continue
        gens = [str(g.get("id")) for g in (ev.get("generations") or [])]
        co = m["aggregate"].get("coevolution") or {}
        eval_block = None
        if co.get("measurable"):
            summary = (co.get("flow") or {}).get("summary") or {}
            adopted = [mid for eg in (co.get("eval_generations") or []) if eg.get("after_step") for mid in (eg.get("adopted") or [])]
            eval_block = {"generations": len(co.get("eval_generations") or []), "adopted": adopted,
                          "closures": summary.get("closures"), "closures_learned": summary.get("closures_learned"),
                          "drift": ((co.get("integrity") or {}).get("drift") or {}).get("jaccard_distance_from_base"),
                          "recommended": {k: (co.get("recommended") or {}).get(k) for k in ("base", "evolved", "agree")},
                          "synthetic": bool(co.get("synthetic"))}
            loops.append({"agent_family": str(ev["family"]), "member": label, "eval_generations": eval_block["generations"],
                          "closures": summary.get("closures"), "closures_learned": summary.get("closures_learned"),
                          "reading": summary.get("sentence")})
        verdicts: dict = {}
        for step in ev.get("steps") or []:
            v = step.get("verdict") or "unmeasurable"
            verdicts[v] = verdicts.get(v, 0) + 1
        lineages.append({"family": str(ev["family"]), "member": label, "generations": gens, "generations_n": len(gens),
                         "recommended": (ev.get("recommended") or {}).get("id"), "best": (ev.get("best") or {}).get("id"),
                         "verdicts": dict(sorted(verdicts.items())), "eval": eval_block,
                         "loops": eval_block["closures_learned"] if eval_block else None,
                         "integrity": (ev.get("integrity") or {}).get("reading"), "measurable": bool(ev.get("measurable")),
                         "reason": ev.get("reason")})
    return lineages, loops


def _overview(members: list, labels: list, rows: list, token_cap: Any) -> dict:
    lineages, loops = _lineages(members, labels)
    gen_family = {g: ln["family"] for ln in lineages for g in ln["generations"]}
    by_agent: dict = {}
    for r in rows:
        by_agent.setdefault(r["agent"], []).append(r)
    agents = []
    for name in sorted(by_agent):
        own = by_agent[name]
        family = None
        for ln in lineages:
            if name.startswith(ln["family"] + "@") and name.split("@", 1)[1] in gen_family:
                family = ln["family"]
        tokens, tokens_runs = _sum_known(own, "tokens")
        cost, cost_runs = _sum_known(own, "cost_usd")
        seconds, seconds_runs = _sum_known(own, "seconds")
        fetches, fetches_runs = _sum_known(own, "fetches")
        framework = None
        for m, label in zip(members, labels):
            framework = framework or _framework_of(m, name)
        agents.append({"name": name, "family": family, "framework": framework, "runs": len(own),
                       "tasks": len({r["task"] for r in own}), "success_rate": _success(own),
                       "tokens_total": tokens, "tokens_runs": tokens_runs,
                       "cost_usd_total": round(cost, 6) if cost is not None else None, "cost_runs": cost_runs,
                       "seconds_total": rounded(seconds), "seconds_runs": seconds_runs,
                       "fetches_total": fetches, "fetches_runs": fetches_runs,
                       "self_evolving": family is not None, "lineage": family,
                       "synthetic": any(r["synthetic"] for r in own),
                       "members": sorted({r["member"] for r in own})})
    tokens, tokens_runs = _sum_known(rows, "tokens")
    cost, cost_runs = _sum_known(rows, "cost_usd")
    seconds, _ = _sum_known(rows, "seconds")
    fetches, fetches_runs = _sum_known(rows, "fetches")
    totals = {"runs": len(rows), "tasks": len({r["task"] for r in rows}), "agents": len(agents), "members": len(members),
              "tokens": tokens, "tokens_runs": tokens_runs, "cost_usd": round(cost, 6) if cost is not None else None,
              "cost_runs": cost_runs, "seconds": rounded(seconds), "fetches": fetches, "fetches_runs": fetches_runs,
              "synthetic_share": rounded(sum(1 for r in rows if r["synthetic"]) / len(rows)) if rows else None,
              "basis": "sums over the runs that recorded the quantity; *_runs says how many did"}
    heaviest = sorted((r for r in rows if finite(r.get("tokens"))), key=lambda r: (-r["tokens"], r["key"]))[:HEAVIEST]
    if finite(token_cap):
        cap = {"value": int(token_cap), "source": "--token-cap",
               "over": [r["key"] for r in sorted(rows, key=lambda r: r["key"]) if finite(r.get("tokens")) and r["tokens"] > token_cap]}
    else:
        cap = {"value": None, "source": "none given", "over": []}
    present = set()
    for m in members:
        present.update(m["sections"])
    overview = {"agents": agents, "lineages": lineages, "loops": loops, "totals": totals, "sections": sorted(present),
                "heaviest_runs": [{"key": r["key"], "tokens": r["tokens"]} for r in heaviest], "cap": cap}
    overview["reading"] = _reading(overview)
    return overview


def _reading(o: dict) -> str:
    t = o["totals"]
    parts = [f"{plural(t['members'], 'member')}: {plural(t['agents'], 'agent')} over {plural(t['tasks'], 'task')} and {plural(t['runs'], 'run')}"]
    parts.append(f"{num(t['tokens'])} tokens counted over {t['tokens_runs']} of them" if t["tokens"] is not None else "no run recorded tokens")
    if t["cost_usd"] is not None:
        parts.append(f"{num(t['cost_usd'], 4)} USD recorded over {plural(t['cost_runs'], 'run')}")
    else:
        parts.append("no run recorded a cost")
    parts.append(f"{num(t['fetches'])} fetches over {t['fetches_runs']}" if t["fetches"] is not None else "no fetch counted")
    evolving = [a["name"] for a in o["agents"] if a["self_evolving"]]
    if o["lineages"]:
        bits = []
        for ln in o["lineages"]:
            bit = f"{ln['family']} ({plural(ln['generations_n'], 'generation')}, {ln['recommended'] or 'none'} recommended"
            if ln["eval"]:
                bit += f"; its eval evolved through {plural(ln['eval']['generations'], 'generation')} and closed {ln['eval']['closures'] or 0} loops"
            bits.append(bit + ")")
        parts.append(f"{plural(len(o['lineages']), 'lineage')} of {plural(len(evolving), 'self-evolving agent')}: {join_names(bits)}")
    else:
        parts.append("no self-evolving agent")
    if o["heaviest_runs"]:
        h = o["heaviest_runs"][0]
        parts.append(f"the heaviest run is {h['key']} at {num(h['tokens'])} tokens")
    if o["cap"]["value"] is not None:
        parts.append(f"{plural(len(o['cap']['over']), 'run')} over the cap of {num(o['cap']['value'])}")
    share = t["synthetic_share"]
    parts.append(f"SYNTHETIC share {pct(share)}" if share else "no run is labelled SYNTHETIC")
    return "; ".join(parts) + "."


def _member_sections(m: dict) -> tuple:
    """``(budget, fetches)`` of one member: the aggregate's sections when
    it carries them, else the same readings derived from the reports'
    sides, each saying which."""
    agg = m["aggregate"]
    if (agg.get("budget") or {}).get("measurable") and (agg.get("fetches") or {}).get("measurable"):
        return dict(agg["budget"], source="aggregate"), dict(agg["fetches"], source="aggregate")
    trajs = [t for report in m["reports"] for side in ("a", "b") for t in [_traj_of_side(report, side)] if t is not None]
    return (dict(budget_aggregate(trajs), source="derived from the pair reports' sides"),
            dict(fetches_aggregate(trajs), source="derived from the pair reports' sides"))


def levels(members: list, token_cap: Any = None, traces: Optional[list] = None) -> dict:
    """The three levels from the members: ``overview``, ``runs`` (the
    rows), ``records`` (the level-3 record per key), ``run_index`` (key →
    path under the bundle), per member the ``budget`` and ``fetches``
    aggregates, and ``traces`` — the trace files (:func:`load_traces`
    entries) that completed a record, each with the member and the
    bundle-relative path it is copied to. ``traces`` given completes every
    record whose steps are not in the output from the matching trace;
    absent, nothing here changes."""
    labels = _labels(members)
    rows: list = []
    records: dict = {}
    run_index: dict = {}
    taken: set = set()
    budgets: dict = {}
    fetches: dict = {}
    pool = _TracePool(traces or [])
    copies: dict = {}
    used: list = []
    for m, label in zip(members, labels):
        mrows, details, ids = _member_rows(m, label)
        for key in sorted(mrows):
            row = mrows[key]
            detail = details.get(key)
            if traces and not (detail and detail.get("steps")):
                entry = pool.match(ids.get(key), key)
                if entry is not None:
                    rel = _trace_file(label, entry["rel"], copies, entry["path"])
                    if not any(u["rel"] == rel for u in used):
                        used.append({"member": label, "rel": rel, "source": entry["path"]})
                    detail = _complete(row, detail, entry, rel)
            rows.append(row)
            records[row["key"]] = _record(row, detail)
            run_index[row["key"]] = _run_file(row["key"], taken)
        budgets[label], fetches[label] = _member_sections(m)
    return {"overview": _overview(members, labels, rows, token_cap), "runs": rows, "records": records,
            "run_index": run_index, "budget": budgets, "fetches": fetches, "labels": labels, "traces": used}


def select_runs(rows: list, agent: Optional[str] = None, task: Optional[str] = None, member: Optional[str] = None,
                success: Optional[bool] = None, sort: Optional[str] = None, limit: Optional[int] = None) -> list:
    """The level-2 rows filtered and sorted: ``sort`` is one of
    :data:`SORT_FIELDS`, descending, rows without the number last;
    ``ValueError`` for an unknown sort."""
    out = [r for r in rows if (agent is None or r["agent"] == agent) and (task is None or r["task"] == task)
           and (member is None or r["member"] == member) and (success is None or r["success"] is success)]
    if sort is not None:
        if sort not in SORT_FIELDS:
            raise ValueError(f"sort must be one of {', '.join(SORT_FIELDS)}, not {sort!r}")
        field = SORT_FIELDS[sort]
        out.sort(key=lambda r: (0 if finite(r.get(field)) else 1, -(r.get(field) or 0), r["key"]))
    if limit is not None:
        out = out[: max(0, int(limit))]
    return out


# ---------------------------------------------------------------- the key

def _key_payload(info: dict, agents_kept: int, with_locators: bool, with_eval: bool) -> dict:
    o = info["overview"]
    agents = [{"name": a["name"], "runs": a["runs"],
               "success_rate": {k: a["success_rate"][k] for k in ("rate", "lo", "hi")},
               "tokens_total": a["tokens_total"], "self_evolving": a["self_evolving"]} for a in o["agents"][:agents_kept]]
    lineages = [{"family": ln["family"], "generations": ln["generations_n"], "recommended": ln["recommended"],
                 "eval_adopted": (ln["eval"]["adopted"] if ln["eval"] and with_eval else None)} for ln in o["lineages"]]
    t = o["totals"]
    return {"v": KEY_VERSION, "id": info["id"], "name": info["name"], "agents": agents,
            "truncated": max(0, len(o["agents"]) - agents_kept), "lineages": lineages,
            "totals": {k: t[k] for k in ("runs", "tasks", "agents", "tokens", "cost_usd", "seconds", "fetches", "synthetic_share")},
            "locators": list(info.get("locators") or []) if with_locators else []}


def _encode(payload: dict) -> str:
    raw = zlib.compress(canonical(payload), 9)
    return KEY_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def encode_key(info: dict) -> str:
    """The key of a bundle: ``agentdiff1:<base64url(zlib(json))>`` of the
    level-1 overview and the id, kept under :data:`KEY_MAX_BYTES` by
    naming at most :data:`KEY_AGENTS` agents and, past that, dropping the
    locators first, then naming fewer agents one at a time, then dropping
    the evals' adopted metrics — each drop counted or nulled in the key,
    never silent. ``info`` is what :func:`build` returns (``id``, ``name``,
    ``overview``, ``locators``)."""
    kept = min(KEY_AGENTS, len(info["overview"]["agents"]))
    locators, evals = True, True
    while True:
        key = _encode(_key_payload(info, kept, locators, evals))
        if len(key) <= KEY_MAX_BYTES:
            return key
        if locators:
            locators = False
        elif kept > 0:
            kept -= 1
        elif evals:
            evals = False
        else:
            raise ValueError(f"the key cannot be kept under {KEY_MAX_BYTES} bytes")


def _check_key_payload(payload: Any) -> None:
    """``ValueError`` unless ``payload`` has the shape :func:`_key_payload`
    writes — every field the ``key`` command prints, of the type it
    expects — so a tampered key that still decodes is refused with a
    reason rather than crashing the reader."""
    def bad(what: str) -> ValueError:
        return ValueError(f"not an AgentDiff key: {what}")

    if not isinstance(payload, dict) or payload.get("v") != KEY_VERSION or not isinstance(payload.get("id"), str):
        raise bad("the payload is not a version-1 overview with an id")
    if payload.get("name") is not None and not isinstance(payload["name"], str):
        raise bad("name is not a string")
    agents = payload.get("agents", [])
    if not isinstance(agents, list):
        raise bad("agents is not a list")
    for i, a in enumerate(agents):
        if not isinstance(a, dict) or not isinstance(a.get("name"), str):
            raise bad(f"agents[{i}] is not an object with a name")
        if not isinstance(a.get("runs"), int) or isinstance(a.get("runs"), bool):
            raise bad(f"agents[{i}].runs is not an integer")
        if a.get("success_rate") is not None and not isinstance(a["success_rate"], dict):
            raise bad(f"agents[{i}].success_rate is not an object")
        if a.get("tokens_total") is not None and not finite(a["tokens_total"]):
            raise bad(f"agents[{i}].tokens_total is not a number")
    truncated = payload.get("truncated", 0)
    if not isinstance(truncated, int) or isinstance(truncated, bool) or truncated < 0:
        raise bad("truncated is not a count")
    lineages = payload.get("lineages", [])
    if not isinstance(lineages, list):
        raise bad("lineages is not a list")
    for i, ln in enumerate(lineages):
        if not isinstance(ln, dict) or not isinstance(ln.get("family"), str):
            raise bad(f"lineages[{i}] is not an object with a family")
        if not isinstance(ln.get("generations"), int) or isinstance(ln.get("generations"), bool):
            raise bad(f"lineages[{i}].generations is not an integer")
        if ln.get("recommended") is not None and not isinstance(ln["recommended"], str):
            raise bad(f"lineages[{i}].recommended is not a string")
        adopted = ln.get("eval_adopted")
        if adopted is not None and not (isinstance(adopted, list) and all(isinstance(x, str) for x in adopted)):
            raise bad(f"lineages[{i}].eval_adopted is not a list of metric ids")
    if payload.get("totals") is not None and not isinstance(payload["totals"], dict):
        raise bad("totals is not an object")
    locators = payload.get("locators", [])
    if not isinstance(locators, list) or not all(isinstance(x, str) for x in locators):
        raise bad("locators is not a list of strings")


def decode_key(text: str) -> dict:
    """The overview a key carries; ``ValueError`` with the reason when the
    text is not a key — the wrong prefix, not printable ASCII, longer than
    :data:`KEY_MAX_BYTES` (no key :func:`encode_key` writes is), a payload
    past :data:`KEY_DECODED_MAX` once decompressed, or a payload whose
    shape is not the overview's."""
    text = (text or "").strip()
    if not text.startswith(KEY_PREFIX):
        raise ValueError(f"not an AgentDiff key: it does not start with {KEY_PREFIX!r}")
    if len(text) > KEY_MAX_BYTES:
        raise ValueError(f"not an AgentDiff key: {len(text)} bytes, over the {KEY_MAX_BYTES} a key can be")
    body = text[len(KEY_PREFIX):]
    if not body or any(ord(c) > 126 or ord(c) < 33 for c in body):
        raise ValueError("not an AgentDiff key: the body is empty or not printable ASCII")
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        inflater = zlib.decompressobj()
        data = inflater.decompress(raw, KEY_DECODED_MAX)
        if inflater.unconsumed_tail or not inflater.eof:
            raise ValueError(f"the payload decompresses past {KEY_DECODED_MAX} bytes or is cut short")
        payload = json.loads(data.decode("utf-8"))
    except (binascii.Error, ValueError, zlib.error, UnicodeDecodeError) as exc:
        raise ValueError(f"not an AgentDiff key: {exc}") from exc
    _check_key_payload(payload)
    return payload


# --------------------------------------------------------------- building

def build(members: list, name: Optional[str] = None, token_cap: Any = None, locators: Any = (), traces: Any = ()) -> dict:
    """Everything the bundle holds, in memory: ``id``, ``name``,
    ``members`` (the index rows), ``levels``, ``records``, ``locators``,
    ``key``, and ``traces`` — ``{dirs, read, notes, completed, files}``
    when trace directories were given (the id is unchanged by them: it
    is the members' content)."""
    if not members:
        raise ValueError("a bundle needs at least one output directory")
    entries, notes = load_traces(traces)
    lv = levels(members, token_cap, entries if traces else None)
    labels = lv.pop("labels")
    used = lv.pop("traces")
    index = [{"index": i, "label": label, "kind": m["kind"], "source": m["source"], "tasks": m["tasks"], "agents": m["agents"],
              "lineage": m["lineage"], "sections": m["sections"], "runs": sum(1 for r in lv["runs"] if r["member"] == label)}
             for i, (m, label) in enumerate(zip(members, labels))]
    info = {"version": VERSION, "id": digest(members), "name": name or "+".join(labels), "members": index,
            "overview": lv["overview"], "runs": lv["runs"], "run_index": lv["run_index"], "budget": lv["budget"],
            "fetches": lv["fetches"], "records": lv["records"], "locators": [str(x) for x in (locators or [])],
            "traces": {"dirs": [str(d) for d in (traces or [])], "read": len(entries), "notes": notes,
                       "completed": sum(1 for r in lv["records"].values() if r.get("steps_source")), "files": used}}
    info["records_digest"] = records_digest(lv["records"], {t["rel"]: Path(t["source"]).read_bytes() for t in used})
    info["key"] = encode_key(info)
    return info


def _dump(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_bundle(members: list, out_dir: Union[str, Path], template: Union[str, Path], name: Optional[str] = None,
                 token_cap: Any = None, locators: Any = (), traces: Any = ()) -> dict:
    """Build and write the bundle under ``out_dir``: ``bundle.json``, the
    members' copies, ``runs/<key>.json`` per run, with ``traces`` the
    copies under ``traces/<member>/…`` of every trace that completed a
    record, ``report.html`` (the primary member's page with the bundle
    inlined) and ``KEY.txt``. An existing bundle at ``out_dir`` is
    replaced: ``members/``, ``runs/`` and ``traces/`` are cleared first,
    so nothing stale outlives the manifest that no longer names it.
    Returns :func:`build`'s dict plus ``files``."""
    info = build(members, name=name, token_cap=token_cap, locators=locators, traces=traces)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for stale in ("members", "runs", "traces"):
        if (out / stale).is_dir():
            shutil.rmtree(out / stale)
    files: list = []
    levels_block = {"overview": info["overview"], "runs": info["runs"], "run_index": info["run_index"],
                    "budget": info["budget"], "fetches": info["fetches"]}
    manifest = {"version": VERSION, "id": info["id"], "records_digest": info["records_digest"], "name": info["name"],
                "members": info["members"], "levels": levels_block, "locators": info["locators"], "key": info["key"]}
    _dump(out / "bundle.json", manifest)
    files.append("bundle.json")
    for i, m in enumerate(members):
        dest = out / "members" / str(i)
        dest.mkdir(parents=True, exist_ok=True)
        src = Path(m["path"])
        for candidate in sorted(src.glob("*.json")):
            if candidate.name in ("aggregate.json", "fleet.json") or candidate.name.startswith("report_"):
                shutil.copyfile(candidate, dest / candidate.name)
                files.append(f"members/{i}/{candidate.name}")
    (out / "runs").mkdir(exist_ok=True)
    for key, rel in info["run_index"].items():
        _dump(out / rel, info["records"][key])
        files.append(rel)
    for t in info["traces"]["files"]:
        dest = out / t["rel"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(t["source"], dest)
        files.append(t["rel"])
    primary = members[0]
    page = {"id": info["id"], "name": info["name"], "members": info["members"],
            "levels": dict(levels_block, records=info["records"])}
    render_html(primary["reports"], primary["aggregate"], template, out / "report.html", fleet=primary.get("fleet"),
                extra={"bundle": page})
    files.append("report.html")
    (out / "KEY.txt").write_text(info["key"] + "\n", encoding="utf-8")
    files.append("KEY.txt")
    info["files"] = files
    return info


# ---------------------------------------------------------------- reading

def verify(bundle_dir: Union[str, Path]) -> dict:
    """Recompute the id from the members' copies and the records digest
    from ``runs/*.json`` and the copied traces: ``{id, recomputed, match,
    records_digest, records_recomputed, records_match, records_reason}``
    — the two checked and reported separately, since the id is the
    members' content and says nothing about the run records; ``ValueError``
    when the directory is not a bundle."""
    root = Path(bundle_dir)
    manifest_path = root / "bundle.json"
    if not manifest_path.is_file():
        raise ValueError(f"{root} holds no bundle.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    members = []
    for i in range(len(manifest.get("members") or [])):
        members.append(read_member(root / "members" / str(i)))
    recomputed = digest(members)
    records: dict = {}
    traces: dict = {}
    missing: list = []
    for key, rel in ((manifest.get("levels") or {}).get("run_index") or {}).items():
        path = root / str(rel)
        if not path.is_file():
            missing.append(str(rel))
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        records[key] = record
        source = str(record.get("steps_source") or "")
        if source.startswith("trace "):
            trel = source[len("trace "):]
            if (root / trel).is_file():
                traces[trel] = (root / trel).read_bytes()
            else:
                missing.append(trel)
    records_recomputed = records_digest(records, traces)
    claimed = manifest.get("records_digest")
    if claimed is None:
        reason = "the manifest carries no records_digest"
    elif missing:
        reason = f"missing from the bundle: {', '.join(missing[:5])}" + (f" and {len(missing) - 5} more" if len(missing) > 5 else "")
    elif claimed != records_recomputed:
        reason = "a run record or a copied trace differs from what the manifest recorded"
    else:
        reason = None
    return {"id": manifest.get("id"), "recomputed": recomputed, "match": manifest.get("id") == recomputed,
            "records_digest": claimed, "records_recomputed": records_recomputed, "records_match": reason is None,
            "records_reason": reason}


class Bundle:
    """A bundle on disk, read lazily: the manifest at once, a run's record
    when asked. What the MCP server and the HTTP API serve."""

    def __init__(self, bundle_dir: Union[str, Path]) -> None:
        self.path = Path(bundle_dir)
        manifest = self.path / "bundle.json"
        if not manifest.is_file():
            raise ValueError(f"{self.path} holds no bundle.json")
        self.manifest = json.loads(manifest.read_text(encoding="utf-8"))
        if self.manifest.get("version") != VERSION:
            raise ValueError(f"{manifest} is bundle version {self.manifest.get('version')!r}; this reader knows {VERSION}")

    @property
    def id(self) -> str:
        return str(self.manifest.get("id"))

    @property
    def name(self) -> str:
        return str(self.manifest.get("name"))

    @property
    def key(self) -> str:
        return str(self.manifest.get("key"))

    @property
    def members(self) -> list:
        return list(self.manifest.get("members") or [])

    @property
    def levels(self) -> dict:
        return self.manifest.get("levels") or {}

    @property
    def overview(self) -> dict:
        return self.levels.get("overview") or {}

    @property
    def rows(self) -> list:
        return list(self.levels.get("runs") or [])

    @property
    def run_index(self) -> dict:
        return dict(self.levels.get("run_index") or {})

    def runs(self, **filters: Any) -> list:
        return select_runs(self.rows, **filters)

    def run(self, key: str) -> Optional[dict]:
        """The level-3 record, or None when the key names no run."""
        rel = self.run_index.get(key)
        if rel is None:
            return None
        return json.loads((self.path / rel).read_text(encoding="utf-8"))

    def data(self, key: str) -> Optional[dict]:
        """The data reading of one run's level-3 record — the prompt, the
        instructions, the models, the corpus, the provenance, the chain —
        or None when the key names no run."""
        record = self.run(key)
        return None if record is None else record.get("data")

    def step(self, key: str, index: int) -> dict:
        """The full text of one step, uncapped, read from the member's copy
        of the report the record came from — or from the bundle's copy of
        the trace when the record was completed from one (``steps_source``):
        ``{key, index, type, name, input, output, input_chars, output_chars,
        tokens, tokens_basis, latency_s, error, effect, quality, note,
        model, span, source}``. ``KeyError`` when the key names no run,
        ``ValueError`` with the reason when the run's steps are not in the
        output or the index names no step."""
        record = self.run(key)
        if record is None:
            raise KeyError(key)
        source = str(record.get("steps_source") or "")
        if not record.get("measurable") or not (record.get("report") or source.startswith("trace ")):
            raise ValueError(f"the steps of {key!r} are not in the output: {record.get('reason')}")
        if source.startswith("trace "):
            rel = source[len("trace "):]
            path = self.path / rel
            if not path.is_file():
                raise ValueError(f"the bundle's copy of the trace {rel} is not in the bundle")
            steps = json.loads(path.read_text(encoding="utf-8")).get("steps") or []
            where = f"{rel}#steps"
        else:
            member = next((m for m in self.members if m.get("label") == record.get("member")), None)
            if member is None:
                raise ValueError(f"the record of {key!r} names a member the bundle does not hold: {record.get('member')!r}")
            path = self.path / "members" / str(member["index"]) / str(record["report"])
            if not path.is_file():
                raise ValueError(f"the member's copy of {record['report']} is not in the bundle")
            report = json.loads(path.read_text(encoding="utf-8"))
            steps = ((report.get(record.get("side")) or {}).get("steps") or [])
            where = f"members/{member['index']}/{record['report']}#{record.get('side')}.steps"
        if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= len(steps):
            raise ValueError(f"{key!r} has {len(steps)} steps, indexed 0 to {len(steps) - 1}; no step {index!r}")
        st = steps[index] if isinstance(steps[index], dict) else {}
        return {"key": key, "index": index, "type": st.get("type"), "name": st.get("name") or "",
                "input": str(st.get("input") or ""), "output": str(st.get("output") or ""),
                "input_chars": len(str(st.get("input") or "")), "output_chars": len(str(st.get("output") or "")),
                "tokens": st.get("tokens"), "tokens_basis": st.get("tokens_basis"), "latency_s": st.get("latency_s"),
                "error": st.get("error"), "effect": st.get("effect"), "quality": st.get("quality"), "note": st.get("note"),
                "model": st.get("model"), "span": st.get("span"), "source": f"{where}[{index}]"}

    def budget(self, agent: Optional[str] = None, task: Optional[str] = None) -> dict:
        """The per-member budget aggregates, narrowed to one agent and/or task."""
        return _narrow(self.levels.get("budget") or {}, agent, task)

    def fetches_summary(self, agent: Optional[str] = None) -> dict:
        return _narrow(self.levels.get("fetches") or {}, agent, None)

    def lineage(self, family: Optional[str] = None) -> dict:
        o = self.overview
        lineages = [ln for ln in (o.get("lineages") or []) if family is None or ln.get("family") == family]
        loops = [lp for lp in (o.get("loops") or []) if family is None or lp.get("agent_family") == family]
        return {"lineages": lineages, "loops": loops,
                "reason": None if lineages else ("no lineage in the bundle" if family is None else f"no lineage {family!r} in the bundle")}

    def verify(self) -> dict:
        return verify(self.path)

    def resources(self) -> list:
        out = [{"uri": RESOURCE_ROOT + "bundle.json", "name": "bundle.json", "mimeType": "application/json"}]
        for m in self.members:
            out.append({"uri": f"{RESOURCE_ROOT}members/{m['index']}/aggregate.json",
                        "name": f"members/{m['index']}/aggregate.json", "mimeType": "application/json"})
        for key in self.run_index:
            out.append({"uri": f"{RESOURCE_ROOT}runs/{key}.json", "name": f"runs/{key}.json", "mimeType": "application/json"})
        return out

    def read_resource(self, uri: str) -> str:
        """The text of one resource; ``KeyError`` when the URI names none."""
        if not uri.startswith(RESOURCE_ROOT):
            raise KeyError(uri)
        rel = uri[len(RESOURCE_ROOT):]
        if rel == "bundle.json":
            return (self.path / "bundle.json").read_text(encoding="utf-8")
        m = re.fullmatch(r"members/(\d+)/aggregate\.json", rel)
        if m and int(m.group(1)) < len(self.members):
            path = self.path / "members" / m.group(1) / "aggregate.json"
            if path.is_file():
                return path.read_text(encoding="utf-8")
            fleet = self.path / "members" / m.group(1) / "fleet.json"
            if fleet.is_file():
                return fleet.read_text(encoding="utf-8")
        if rel.startswith("runs/") and rel.endswith(".json"):
            key = rel[len("runs/"):-len(".json")]
            file = self.run_index.get(key)
            if file is not None:
                return (self.path / file).read_text(encoding="utf-8")
        raise KeyError(uri)


def _narrow(per_member: dict, agent: Optional[str], task: Optional[str]) -> dict:
    out: dict = {}
    for label, section in per_member.items():
        block = dict(section)
        if agent is not None:
            block["agents"] = {k: v for k, v in (section.get("agents") or {}).items() if k == agent}
            block["runs"] = [r for r in (section.get("runs") or []) if r.get("agent") == agent]
            block["heaviest_runs"] = [r for r in (section.get("heaviest_runs") or []) if r.get("agent") == agent]
        if task is not None:
            block["tasks"] = {k: v for k, v in (section.get("tasks") or {}).items() if k == task}
            block["runs"] = [r for r in block.get("runs") or [] if r.get("task") == task]
            block["heaviest_runs"] = [r for r in block.get("heaviest_runs") or [] if r.get("task") == task]
        out[label] = block
    return out


__all__ = ["VERSION", "KEY_PREFIX", "KEY_MAX_BYTES", "KEY_AGENTS", "KEY_DECODED_MAX", "SORT_FIELDS", "RESOURCE_ROOT",
           "canonical", "digest", "records_digest", "read_member", "load_traces", "levels", "select_runs", "encode_key", "decode_key", "build", "write_bundle", "verify", "Bundle"]
