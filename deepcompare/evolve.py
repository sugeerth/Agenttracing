"""A lineage read step by step: did each evolution help, what changed, and
which generation to keep.

A self-evolving agent is not two agents but a *lineage* g0 → g1 → g2 → …,
each generation derived from its parent by one step — a prompt edit, a
rule, a skill, a config change, a memory written — triggered by evidence
from the parent's own episodes. This module reads a lineage directory
(``<lineage>/<gen>/agent.json`` + ``<gen>/traces/*.json`` in the runs
layout, or the flat layout, see :func:`read_lineage`) and answers, per
step and with an interval, the questions the pair report answers for one
A/B: did the step help, on which tasks, and did it go wrong in one of the
ways evolution goes wrong.

**What is computed, and from what.** Every parent → child edge is read as
an ordinary two-policy RL batch — :func:`deepcompare.rl.rl_aggregate` over
the two generations' traces with ``names=(from, to)`` — and the step's
effect is read off that block: the probability of improvement P(to >
from) and both generations' IQM return with their stratified-bootstrap
intervals (:mod:`deepcompare.rlstats`), the per-task return deltas, the
pass rates (a count of ``outcome.success`` over the traces), the reward
audit (:mod:`deepcompare.rlaudit`) and the behaviour distance and branch
points (:mod:`deepcompare.rlspace`). A generation's own IQM is read from
whichever edge block carries it — the per-policy bootstrap stream is
seeded by the policy's name, so the interval is the same from either
edge — and only a generation with no measurable edge (a lineage of one,
or empty neighbours) gets a one-policy block of its own. Beside the
behaviour, the artifacts: each step's diff of the agent's own parts
(:func:`diff_artifacts`) — a unified diff for text, a set difference for
lists, a by-name comparison for named lists, key by key for a flat dict —
its sizes, and a SHA-256 digest of the canonical artifacts JSON. The
expensive part is one ``rl_aggregate`` per edge; nothing is computed
twice, and a seven-generation lineage of 210 traces reads in seconds.

**The checks**, every one a comparison of counts or sums over recorded
steps, each with ``measurable: False`` and a reason when it cannot be read:

* *gaming* — the mean return rose (``return_delta > 0``) while the pass
  rate did not (``pass_delta <= 0``): the step bought reward, not
  correctness. No margin: a return that rises at all without a pass to
  show for it is the shape a gamed reward has, and the reading carries
  both numbers so a reader can weigh a small one.
* *forgetting* — some task's pass rate fell (``regressed`` non-empty)
  while no task's rose and the IQM did not fall: the average hid a loss.
* *overfitting* — the tasks whose episodes triggered the step
  (``evidence.episodes``, resolved against the parent's traces) gained
  more than the held-out tasks by at least :data:`OVERFIT_MARGIN` return
  units, the trigger tasks gained at all, and the held-out tasks did not
  themselves gain by that margin. Needs trigger tasks *and* held-out
  tasks to exist.
* *trading* — ``gained`` and ``regressed`` both non-empty with the IQM
  flat or up: one task bought with another.
* *integrity* — a step changed a protected path (``lineage.json``
  ``protected``: the agent's own verifier, grader, reward config). An
  edit to the judge is a finding unless it plainly puts the judge back:
  re-adds a removed tool or skill, raises a number, turns a switch on.
  Those are ``restored``; everything else — a removal, a decrease, a
  switch turned off, a body or a prompt rewritten — is ``touched``. The
  taint persists: a generation that inherits a weakened judge is as
  ineligible as the one that weakened it, until a step restores the path.
  The direction reading assumes higher, true and present mean stronger,
  which holds for a count of checks and an on-switch and is stated as a
  limit for anything else.
* *growth* — prompt characters, rules, memory notes, skills and tools per
  generation against ``lineage.json`` ``budget``; over budget is a
  finding, never an error.
* *drift* — the normalised edit distance between the generations' token
  streams (:func:`deepcompare.rlspace.normalised_distance`): consecutive
  from each edge's ``space`` block, from the origin computed once over
  every generation's streams against the root's.

**The verdict** is exactly one word per step, in this precedence, and
``null`` when the effect cannot be measured (a generation with no traces,
no shared task) rather than a word that pretends it was:

1. ``gamed``  — ``gaming.flag``.
2. ``forgot`` — ``regressed`` non-empty, ``gained`` empty, IQM delta ≥ 0.
3. ``overfit`` — ``overfit.flag``.
4. ``traded`` — ``gained`` and ``regressed`` both non-empty, IQM delta ≥ 0.
5. ``improved`` when the improvement interval's low end clears 0.5,
   ``regressed`` when its high end sits under 0.5, else ``flat``.

``forgot`` and ``traded`` are disjoint by construction (one needs
``gained`` empty, the other non-empty), so the precedence never has to
choose between them.

**Best and recommended.** ``best`` is the generation with the highest
IQM point (the earliest on a tie). ``recommended`` is the eligible
generation with the highest IQM, where eligible means: it has a
measurable IQM, its incoming step was not ``gamed``, and it is not
running with a weakened protected path (see integrity above). It is
``best`` whenever ``best`` is eligible; a later generation replaces an
earlier one only when its IQM point is higher (an interval wholly above
the earlier one's implies that), so on a tie the earlier generation — the
one with fewer steps behind it — is kept. ``why`` names every generation
that was passed over and the reason.

Determinism: every dict iterated for output is sorted, every bootstrap
seeded (the rlstats seed), no timestamps. Honesty: a size that cannot be
measured is ``None``, a check that cannot be read says why, a thin sample
says so on every step through the runs advisory.

Layouts: ``native`` is the directory above; ``flat`` is one runs directory
whose agent names carry the generation (``family@g3``) with a sibling
``agents/<gen>.json`` per generation. A Darwin-Gödel-Machine-style archive
(``archive/<id>/{metadata.json, …}`` with a ``parent_commit`` and a code
diff) is NOT read in this pass: :func:`_read_dgm` is the seam, it raises
``NotImplementedError`` saying so, and the CLI does not offer it.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import math
from pathlib import Path
from typing import Optional

from .rl import GAMMA, rl_aggregate, rl_run_from_trace
from .rlaudit import audit_aggregate
from .rlspace import MAX_DISTANCE_TOKENS, episode_tokens, normalised_distance
from .rlstats import BOOTSTRAP_SAMPLES, METRICS
from .trace import Trajectory

VERSION = 1
#: the timeline is carried for at most this many episodes across the lineage
EPISODE_TIMELINE_CAP = 2000
#: return units by which the trigger tasks must out-gain the held-out tasks
OVERFIT_MARGIN = 2.0
#: memory notes quoted in a step's diff
MEMORY_SAMPLE = 5
#: every verdict a step can carry, in the order the trajectory counts them
VERDICTS = ("improved", "regressed", "flat", "gamed", "forgot", "overfit", "traded")
#: findings a step can carry beside its verdict (``--fail-on`` accepts both)
FINDINGS = ("protected", "over_budget")
#: the known artifact kinds and how each is diffed
ARTIFACT_KINDS = {"system_prompt": "text", "rules": "list", "skills": "named", "tools": "list",
                  "memory": "list", "config": "dict"}
#: the sizes a generation reports, and the artifact each is read off
SIZE_OF = (("prompt_chars", "system_prompt"), ("rules", "rules"), ("skills", "skills"), ("tools", "tools"),
           ("memory", "memory"), ("config_keys", "config"))
#: step families for the timeline
TOOLISH = ("tool_call", "search", "retrieve", "read")
LAYOUTS = ("native", "flat")


# ---------------------------------------------------------------- formatting

def _num(v, places: int = 2) -> str:
    """A number as text with a real minus sign; ``—`` when there is none."""
    if v is None:
        return "—"
    if abs(v - round(v)) < 1e-9:
        text = f"{int(round(v))}"
    else:
        text = f"{v:.{places}f}".rstrip("0").rstrip(".")
    return text.replace("-", "−")


def _signed(v, places: int = 2) -> str:
    if v is None:
        return "—"
    text = _num(v, places)
    return text if v < 0 else f"+{text}"


def _pct(p) -> str:
    return "—" if p is None else f"{round(100 * p):.0f}%"


def _plural(n: int, word: str, plural: Optional[str] = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _r(v, places: int = 4):
    return None if v is None else round(float(v), places)


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _mean(values: list):
    return sum(values) / len(values) if values else None


# ---------------------------------------------------------------- reading a lineage

def _load_json(path: Path) -> tuple:
    """``(value, error)``: the parsed file, or None and why."""
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, f"{path.name} cannot be read: {exc.strerror or exc}"
    except ValueError as exc:
        return None, f"{path.name} is not valid JSON: {exc}"


def _run_id(path: Path) -> Optional[str]:
    parts = path.stem.split("__")
    return parts[2] if len(parts) >= 3 else None


def _load_trace(path: Path):
    """``(trajectory, error)`` — the harness block kept beside the typed
    trajectory as the runs command does, the run id from the filename."""
    try:
        t = Trajectory.from_json(path)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return None, f"{path.name}: {exc}"
    rid = _run_id(path)
    if rid:
        t.run_id = rid
    try:
        harness = json.loads(path.read_text(encoding="utf-8")).get("harness")
    except (OSError, ValueError, AttributeError):
        harness = None
    if isinstance(harness, dict):
        t.harness = harness  # type: ignore[attr-defined]
    return t, None


def _generation(dir_name: str, raw, error: Optional[str], trace_paths: list, family_hint: Optional[str],
                directory: str) -> dict:
    """One generation from its ``agent.json`` (parsed or not) and its
    trace files. Reads everything it can and notes what it could not."""
    notes: list = []
    agent = None
    if raw is not None and not isinstance(raw, dict):
        error = "agent.json is not an object"
    elif isinstance(raw, dict):
        agent = raw
    gid = dir_name
    if agent is not None:
        if isinstance(agent.get("id"), str) and agent["id"]:
            gid = agent["id"]
        else:
            notes.append("agent.json has no id; the directory name stands in")
    parent = None
    if agent is not None:
        if "parent" not in agent:
            notes.append("agent.json has no parent field; read as the root")
        elif agent["parent"] is None or isinstance(agent["parent"], str):
            parent = agent["parent"]
        else:
            notes.append("agent.json parent is not a string; read as the root")
    family = agent.get("family") if agent is not None and isinstance(agent.get("family"), str) else None
    if agent is not None and family is None:
        notes.append("agent.json has no family" + ("; the lineage's is used" if family_hint else ""))
        family = family_hint
    expected = f"{family}@{gid}" if family else None
    by_name: dict = {}
    trace_errors: list = []
    stems: dict = {}
    trace_ids: dict = {}
    for path in trace_paths:
        t, err = _load_trace(path)
        if t is None:
            trace_errors.append(err)
            continue
        by_name.setdefault(t.agent.name, []).append(t)
        stems[path.stem] = t.task.id
        trace_ids[t.trace_id] = t.task.id
    if expected is not None:
        policy = expected
        trajectories = by_name.get(expected, [])
        for name in sorted(by_name):
            if name != expected:
                notes.append(f"{_plural(len(by_name[name]), 'trace')} named {name!r} rather than {expected!r}; skipped")
    elif len(by_name) == 1:
        policy = next(iter(by_name))
        trajectories = by_name[policy]
    else:
        policy = None
        trajectories = []
        if by_name:
            notes.append(f"the traces name {len(by_name)} agents ({', '.join(sorted(by_name))}) and no family says "
                         f"which is this generation's; none used")
    trajectories.sort(key=lambda t: (t.task.id, t.run_id))
    return {"id": gid, "parent": parent, "dir": directory, "agent": agent, "error": error, "notes": notes,
            "family": family, "policy": policy, "trajectories": trajectories, "trace_errors": trace_errors,
            "stems": stems, "trace_ids": trace_ids}


def _read_lineage_json(root: Path) -> dict:
    meta = {"family": None, "protected": [], "budget": {}, "note": None, "error": None}
    path = root / "lineage.json"
    if not path.is_file():
        return meta
    raw, err = _load_json(path)
    if err or not isinstance(raw, dict):
        meta["error"] = err or "lineage.json is not an object"
        return meta
    if isinstance(raw.get("family"), str):
        meta["family"] = raw["family"]
    protected = raw.get("protected")
    meta["protected"] = sorted({p for p in protected if isinstance(p, str) and p}) if isinstance(protected, list) else []
    budget = raw.get("budget")
    meta["budget"] = {k: v for k, v in sorted(budget.items()) if _number(v)} if isinstance(budget, dict) else {}
    if isinstance(raw.get("note"), str):
        meta["note"] = raw["note"]
    return meta


def _read_native(root: Path, meta: dict) -> list:
    gens = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        aj = d / "agent.json"
        if not aj.is_file():
            continue
        raw, err = _load_json(aj)
        traces = sorted((d / "traces").glob("*.json")) if (d / "traces").is_dir() else []
        gens.append(_generation(d.name, raw, err, traces, meta["family"], str(d)))
    return gens


def _read_flat(root: Path, meta: dict) -> list:
    """One runs directory, ``<task>__<family>@<gen>__<run>.json``, with
    ``agents/<gen>.json`` per generation."""
    agents_dir = root / "agents"
    if not agents_dir.is_dir():
        return []
    paths = sorted(p for p in root.glob("*.json") if p.name != "lineage.json")
    by_gen: dict = {}
    for p in paths:
        parts = p.stem.split("__")
        if len(parts) >= 2 and "@" in parts[1]:
            by_gen.setdefault(parts[1].rsplit("@", 1)[1], []).append(p)
    gens = []
    for aj in sorted(agents_dir.glob("*.json")):
        raw, err = _load_json(aj)
        gid = raw["id"] if isinstance(raw, dict) and isinstance(raw.get("id"), str) and raw["id"] else aj.stem
        gens.append(_generation(aj.stem, raw, err, by_gen.get(gid, []), meta["family"], str(aj)))
    return gens


def _read_dgm(root: Path, meta: dict) -> list:  # pragma: no cover - the seam
    """The seam for a Darwin-Gödel-Machine-style archive: ``archive/<id>/
    metadata.json`` with a ``parent_commit`` and a code diff per entry.
    Not read in this pass; a reader would map each entry to a generation
    whose ``artifacts`` carry the code diff as text and whose ``parent``
    is the ``parent_commit``."""
    raise NotImplementedError("the DGM archive layout is not read yet; convert it to the native layout")


def _order(gens: list) -> tuple:
    """``(ordered, basis)``: the parent chain from the single root when it
    visits every generation exactly once, else sorted directory names
    with the reason."""
    by_id: dict = {}
    for g in gens:
        by_id.setdefault(g["id"], []).append(g)
    fallback = sorted(gens, key=lambda g: g["dir"])
    dupes = sorted(k for k, v in by_id.items() if len(v) > 1)
    if dupes:
        return fallback, f"sorted directory names (duplicate generation ids: {', '.join(dupes)})"
    roots = [g for g in gens if g["parent"] is None]
    if len(roots) != 1:
        why = "no generation has parent null" if not roots else \
            f"{len(roots)} roots: {', '.join(sorted(g['id'] for g in roots))}"
        return fallback, f"sorted directory names ({why})"
    children: dict = {}
    for g in gens:
        if g["parent"] is not None:
            children.setdefault(g["parent"], []).append(g)
    for g in gens:
        if g["parent"] is not None and g["parent"] not in by_id:
            return fallback, f"sorted directory names ({g['id']}'s parent {g['parent']!r} is not in the lineage)"
    ordered = [roots[0]]
    seen = {roots[0]["id"]}
    while True:
        kids = children.get(ordered[-1]["id"], [])
        if not kids:
            break
        if len(kids) > 1:
            return fallback, (f"sorted directory names ({ordered[-1]['id']} has {len(kids)} children: "
                              f"{', '.join(sorted(k['id'] for k in kids))}; a branching lineage is not a chain)")
        nxt = kids[0]
        if nxt["id"] in seen:
            return fallback, "sorted directory names (the parent chain loops)"
        ordered.append(nxt)
        seen.add(nxt["id"])
    if len(ordered) != len(gens):
        left = sorted(g["id"] for g in gens if g["id"] not in seen)
        return fallback, f"sorted directory names (the chain from the root does not reach {', '.join(left)})"
    return ordered, "parent chain"


def read_lineage(path, layout: str = "native") -> dict:
    """Read a lineage directory into generations in lineage order.

    Returns ``{path, layout, family, protected, budget, note, measurable,
    reason, order_basis, generations}`` where each generation carries its
    ``id``, ``parent``, the parsed ``agent`` (or ``error``), its
    ``trajectories`` (only those named ``<family>@<id>``) and the
    ``notes`` on whatever could not be read. ``measurable`` is False only
    when no generation could be found at all; a generation that cannot
    be read is kept, with its reason, so the rest of the lineage still is.
    """
    root = Path(path)
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}; choose one of {', '.join(LAYOUTS)}")
    out = {"path": str(root), "layout": layout, "family": None, "protected": [], "budget": {}, "note": None,
           "measurable": True, "reason": None, "order_basis": None, "generations": [], "notes": []}
    if not root.is_dir():
        out.update(measurable=False, reason=f"{root} is not a directory")
        return out
    meta = _read_lineage_json(root)
    out.update(family=meta["family"], protected=meta["protected"], budget=meta["budget"], note=meta["note"])
    if meta["error"]:
        out["notes"].append(meta["error"])
    gens = _read_native(root, meta) if layout == "native" else _read_flat(root, meta)
    if not gens:
        out.update(measurable=False, reason=("no */agent.json under " if layout == "native" else
                                            "no agents/*.json under ") + str(root))
        return out
    ordered, basis = _order(gens)
    out["order_basis"] = basis
    for i, g in enumerate(ordered):
        g["index"] = i
    out["generations"] = ordered
    if out["family"] is None:
        families = sorted({g["family"] for g in ordered if g["family"]})
        out["family"] = families[0] if len(families) == 1 else None
        if len(families) > 1:
            out["notes"].append(f"the generations name {len(families)} families: {', '.join(families)}")
    return out


# ---------------------------------------------------------------- artifacts

def artifact_digest(artifacts) -> Optional[str]:
    """SHA-256 of ``json.dumps(artifacts, sort_keys=True)``; None when
    there are no artifacts."""
    if artifacts is None:
        return None
    return hashlib.sha256(json.dumps(artifacts, sort_keys=True).encode("utf-8")).hexdigest()


def artifact_size(artifacts) -> dict:
    """Prompt characters, rules, skills, tools, memory notes, config keys —
    each None when the artifact is not the type its kind expects."""
    if not isinstance(artifacts, dict):
        return {"measurable": False, "reason": "artifacts is not an object" if artifacts is not None
                else "no artifacts block", **{k: None for k, _ in SIZE_OF}}
    out: dict = {"measurable": True, "reason": None}
    for key, kind in SIZE_OF:
        v = artifacts.get(kind)
        if kind == "system_prompt":
            out[key] = len(v) if isinstance(v, str) else (0 if v is None else None)
        elif kind == "config":
            out[key] = len(v) if isinstance(v, dict) else (0 if v is None else None)
        else:
            out[key] = len(v) if isinstance(v, list) else (0 if v is None else None)
    return out


def _kind_of(kind: str, a, b) -> str:
    if kind in ARTIFACT_KINDS:
        return ARTIFACT_KINDS[kind]
    for v in (a, b):
        if isinstance(v, str):
            return "text"
        if isinstance(v, dict):
            return "dict"
        if isinstance(v, list):
            return "named" if v and all(isinstance(x, dict) for x in v) else "list"
    return "list"


def _item_key(x) -> str:
    return x if isinstance(x, str) else json.dumps(x, sort_keys=True)


def _diff_text(a, b, kind: str, from_id: str, to_id: str) -> dict:
    if (a is not None and not isinstance(a, str)) or (b is not None and not isinstance(b, str)):
        return {"measurable": False, "reason": f"{kind} is not text on both sides", "added": None, "removed": None,
                "hunks": [], "changed": None}
    a_lines, b_lines = (a or "").splitlines(), (b or "").splitlines()
    lines = list(difflib.unified_diff(a_lines, b_lines, fromfile=f"{from_id}/{kind}", tofile=f"{to_id}/{kind}",
                                      lineterm="", n=1))
    hunks: list = []
    for line in lines[2:]:
        if line.startswith("@@"):
            hunks.append([line])
        elif hunks:
            hunks[-1].append(line)
    added = sum(1 for l in lines[2:] for _ in [0] if l.startswith("+"))
    removed = sum(1 for l in lines[2:] for _ in [0] if l.startswith("-"))
    return {"measurable": True, "reason": None, "added": added, "removed": removed,
            "hunks": ["\n".join(h) for h in hunks], "changed": (a or "") != (b or "")}


def _diff_list(a, b, kind: str) -> dict:
    if (a is not None and not isinstance(a, list)) or (b is not None and not isinstance(b, list)):
        return {"measurable": False, "reason": f"{kind} is not a list on both sides", "added": None, "removed": None}
    ka = {_item_key(x) for x in (a or [])}
    kb = {_item_key(x) for x in (b or [])}
    added = [_item_key(x) for x in (b or []) if _item_key(x) not in ka]
    removed = [_item_key(x) for x in (a or []) if _item_key(x) not in kb]
    added = sorted(dict.fromkeys(added))
    removed = sorted(dict.fromkeys(removed))
    if kind == "memory":
        return {"measurable": True, "reason": None, "added": len(added), "removed": len(removed),
                "sample_added": added[:MEMORY_SAMPLE], "sample_removed": removed[:MEMORY_SAMPLE],
                "added_items": added, "removed_items": removed}
    return {"measurable": True, "reason": None, "added": added, "removed": removed}


def _named(entries) -> tuple:
    named: dict = {}
    unnamed = 0
    for e in entries or []:
        if isinstance(e, dict) and isinstance(e.get("name"), str) and e["name"]:
            named[e["name"]] = json.dumps({k: v for k, v in e.items() if k != "name"}, sort_keys=True)
        else:
            unnamed += 1
    return named, unnamed


def _diff_named(a, b, kind: str) -> dict:
    if (a is not None and not isinstance(a, list)) or (b is not None and not isinstance(b, list)):
        return {"measurable": False, "reason": f"{kind} is not a list on both sides", "added": None, "removed": None,
                "changed": None}
    na, ua = _named(a)
    nb, ub = _named(b)
    return {"measurable": True, "reason": None,
            "added": sorted(set(nb) - set(na)), "removed": sorted(set(na) - set(nb)),
            "changed": sorted(n for n in set(na) & set(nb) if na[n] != nb[n]),
            "unnamed": {"from": ua, "to": ub}}


def _diff_dict(a, b, kind: str) -> dict:
    if (a is not None and not isinstance(a, dict)) or (b is not None and not isinstance(b, dict)):
        return {"measurable": False, "reason": f"{kind} is not an object on both sides", "changed": None}
    a, b = a or {}, b or {}
    changed = []
    for key in sorted(set(a) | set(b)):
        if json.dumps(a.get(key), sort_keys=True) != json.dumps(b.get(key), sort_keys=True):
            changed.append({"key": str(key), "from": a.get(key), "to": b.get(key),
                            "added": key not in a, "removed": key not in b})
    return {"measurable": True, "reason": None, "changed": changed}


def _changed(kind_type: str, block: dict) -> bool:
    if not block.get("measurable"):
        return False
    if kind_type == "text":
        return bool(block["changed"])
    if kind_type == "dict":
        return bool(block["changed"])
    if kind_type == "named":
        return bool(block["added"] or block["removed"] or block["changed"])
    return bool(block["added"] or block["removed"])


def _direction_of_values(frm, to) -> str:
    """``restored`` when the change plainly strengthens (a number up, a
    switch on, a key present), ``weakened`` when it plainly does not, else
    ``changed`` — which the integrity rule treats as a finding."""
    if isinstance(frm, bool) and isinstance(to, bool):
        return "restored" if to and not frm else "weakened" if frm and not to else "changed"
    if _number(frm) and _number(to):
        return "restored" if to > frm else "weakened" if to < frm else "changed"
    if to is None and frm is not None:
        return "weakened"
    return "changed"


def _protected_change(diff: dict, path: str) -> Optional[dict]:
    """What a step did to one protected path, or None when it left it alone
    (or the artifact could not be read, which is said in ``diff``)."""
    kind, _, sub = path.partition(".")
    block = diff.get(kind)
    if not isinstance(block, dict) or not block.get("measurable"):
        return None
    kind_type = diff["kinds"].get(kind, "list")
    base = {"path": path, "kind": kind}
    if not sub or kind_type == "text":
        if not _changed(kind_type, block):
            return None
        if kind_type == "text":
            return dict(base, **{"from": f"{block['removed']} lines removed", "to": f"{block['added']} lines added",
                                 "direction": "changed"})
        return dict(base, **{"from": "as before", "to": "changed", "direction": "changed"})
    if kind_type == "dict":
        row = next((c for c in block["changed"] if c["key"] == sub), None)
        if row is None:
            return None
        return dict(base, **{"from": row["from"], "to": row["to"], "direction": _direction_of_values(row["from"], row["to"])})
    if kind_type == "named":
        if sub in block["removed"]:
            return dict(base, **{"from": "present", "to": "absent", "direction": "weakened"})
        if sub in block["added"]:
            return dict(base, **{"from": "absent", "to": "present", "direction": "restored"})
        if sub in block["changed"]:
            return dict(base, **{"from": "as before", "to": "body changed", "direction": "changed"})
        return None
    added = block.get("added_items", block["added"]) if kind == "memory" else block["added"]
    removed = block.get("removed_items", block["removed"]) if kind == "memory" else block["removed"]
    if sub in (removed or []):
        return dict(base, **{"from": "present", "to": "absent", "direction": "weakened"})
    if sub in (added or []):
        return dict(base, **{"from": "absent", "to": "present", "direction": "restored"})
    return None


def _summary(diff: dict) -> str:
    bits: list = []
    cfg = diff.get("config")
    if isinstance(cfg, dict) and cfg.get("measurable"):
        for c in cfg["changed"]:
            frm, to = c["from"], c["to"]
            sign = "~"
            if _number(frm) and _number(to):
                sign = "-" if to < frm else "+"
            elif isinstance(frm, bool) and isinstance(to, bool):
                sign = "+" if to else "-"
            elif c["added"]:
                sign = "+"
            elif c["removed"]:
                sign = "-"
            bits.append(f"{sign}{c['key']} {json.dumps(frm)}→{json.dumps(to)}")
    for kind, word in (("rules", "rule"), ("memory", "note")):
        block = diff.get(kind)
        if isinstance(block, dict) and block.get("measurable"):
            added = block["added"] if isinstance(block["added"], int) else len(block["added"])
            removed = block["removed"] if isinstance(block["removed"], int) else len(block["removed"])
            if added:
                bits.append(f"+{_plural(added, word)}")
            if removed:
                bits.append(f"-{_plural(removed, word)}")
    for kind, word in (("skills", "skill"), ("tools", "tool")):
        block = diff.get(kind)
        if isinstance(block, dict) and block.get("measurable"):
            bits += [f"+{word} {n}" for n in block["added"]]
            bits += [f"-{word} {n}" for n in block["removed"]]
            bits += [f"~{word} {n}" for n in block.get("changed") or []]
    sp = diff.get("system_prompt")
    if isinstance(sp, dict) and sp.get("measurable") and sp["changed"]:
        bits.append(f"prompt +{sp['added']}/-{sp['removed']} lines")
    for kind in sorted(diff["kinds"]):
        if kind in ARTIFACT_KINDS:
            continue
        block = diff.get(kind)
        if isinstance(block, dict) and _changed(diff["kinds"][kind], block):
            bits.append(f"~{kind}")
    unreadable = sorted(k for k in diff["kinds"] if isinstance(diff.get(k), dict) and not diff[k].get("measurable"))
    if unreadable:
        bits.append("unreadable: " + ", ".join(unreadable))
    return "; ".join(bits) if bits else "no artifact changed"


def diff_artifacts(a, b, protected=(), from_id: str = "from", to_id: str = "to") -> dict:
    """The diff of two generations' artifacts, kind by kind.

    Text (``system_prompt``) gets a unified diff (added/removed line counts
    and the hunks); a list of strings (``rules``, ``tools``) the set
    difference, ``memory`` the same as counts with a sample; a named list
    (``skills``) the names added, removed and changed by body; a flat dict
    (``config``) every key whose value changed with ``from`` and ``to``.
    Unknown kinds are diffed by their type. ``protected_touched`` names
    the protected paths the step weakened or changed (a finding),
    ``protected_restored`` the ones it plainly put back, and
    ``protected_changes`` carries each with its direction. When either
    side's artifacts block is not an object the diff is ``measurable:
    False`` with the reason, and nothing else is claimed about it.
    """
    if not isinstance(a, dict) or not isinstance(b, dict):
        which = "neither side" if not isinstance(a, dict) and not isinstance(b, dict) else \
            f"{from_id}" if not isinstance(a, dict) else f"{to_id}"
        return {"measurable": False, "reason": f"artifacts is not an object on {which}", "kinds": {},
                "protected_touched": [], "protected_restored": [], "protected_changes": [],
                "summary": "artifacts unreadable"}
    kinds = {k: _kind_of(k, a.get(k), b.get(k)) for k in sorted(set(a) | set(b))}
    out: dict = {"measurable": True, "reason": None, "kinds": kinds}
    for kind, kind_type in kinds.items():
        if kind_type == "text":
            out[kind] = _diff_text(a.get(kind), b.get(kind), kind, from_id, to_id)
        elif kind_type == "dict":
            out[kind] = _diff_dict(a.get(kind), b.get(kind), kind)
        elif kind_type == "named":
            out[kind] = _diff_named(a.get(kind), b.get(kind), kind)
        else:
            out[kind] = _diff_list(a.get(kind), b.get(kind), kind)
    changes = []
    for path in sorted({p for p in protected if isinstance(p, str) and p}):
        row = _protected_change(out, path)
        if row:
            changes.append(row)
    out["protected_changes"] = changes
    out["protected_touched"] = [c["path"] for c in changes if c["direction"] != "restored"]
    out["protected_restored"] = [c["path"] for c in changes if c["direction"] == "restored"]
    out["summary"] = _summary(out)
    return out


# ---------------------------------------------------------------- evidence

def trigger_tasks(evidence, parent: Optional[dict]) -> dict:
    """The tasks of the evidence episodes: each id resolved against the
    parent's trace filenames and trace ids, falling back to the
    ``<task>__…`` prefix; ``unresolved`` names what matched nothing."""
    if evidence is None:
        return {"tasks": [], "unresolved": [], "reason": "no evidence block"}
    if not isinstance(evidence, dict):
        return {"tasks": [], "unresolved": [], "reason": "evidence is not an object"}
    episodes = evidence.get("episodes")
    if not isinstance(episodes, list):
        return {"tasks": [], "unresolved": [], "reason": "evidence.episodes is not a list"
                if episodes is not None else "evidence names no episodes"}
    stems = (parent or {}).get("stems") or {}
    ids = (parent or {}).get("trace_ids") or {}
    tasks, unresolved = set(), []
    for ep in episodes:
        if not isinstance(ep, str):
            unresolved.append(json.dumps(ep))
            continue
        stem = ep[:-5] if ep.endswith(".json") else ep
        if stem in stems:
            tasks.add(stems[stem])
        elif ep in ids:
            tasks.add(ids[ep])
        elif "__" in stem:
            tasks.add(stem.split("__")[0])
        else:
            unresolved.append(ep)
    return {"tasks": sorted(tasks), "unresolved": unresolved,
            "reason": None if tasks else "no evidence episode names a task"}


# ---------------------------------------------------------------- per-step effect

def _band(row) -> dict:
    row = row if isinstance(row, dict) else {}
    return {"point": row.get("point"), "lo": row.get("lo"), "hi": row.get("hi")}


def _passes(episodes: list) -> tuple:
    n = len(episodes)
    return sum(1 for e in episodes if e.get("success") is True), n


def _per_task_passes(episodes: list) -> dict:
    out: dict = {}
    for e in episodes:
        cell = out.setdefault(str(e.get("task_id")), [0, 0])
        cell[1] += 1
        if e.get("success") is True:
            cell[0] += 1
    return out


def step_effect(block: dict, frm: str, to: str) -> dict:
    """The effect of one step read off its two-policy ``rl`` block."""
    stats = block.get("stats") or {}
    agents = block.get("agents") or {}
    eps_from = (agents.get(frm) or {}).get("episodes") or []
    eps_to = (agents.get(to) or {}).get("episodes") or []
    imp = stats.get("improvement") or {}
    agg = stats.get("aggregates") or {}
    iqm_from, iqm_to = _band(((agg.get(frm) or {}).get("iqm"))), _band(((agg.get(to) or {}).get("iqm")))
    p_from, n_from = _passes(eps_from)
    p_to, n_to = _passes(eps_to)
    pass_from = p_from / n_from if n_from else None
    pass_to = p_to / n_to if n_to else None
    pt_from, pt_to = _per_task_passes(eps_from), _per_task_passes(eps_to)
    tasks_block = block.get("tasks") or {}
    shared = sorted(set(pt_from) & set(pt_to))
    per_task: dict = {}
    for tid in shared:
        cell = tasks_block.get(tid) or {}
        pf = pt_from[tid][0] / pt_from[tid][1]
        pt = pt_to[tid][0] / pt_to[tid][1]
        per_task[tid] = {"delta_return": cell.get("delta"), "p": (imp.get("per_task") or {}).get(tid),
                         "pass_from": _r(pf), "pass_to": _r(pt), "passes_from": pt_from[tid][0],
                         "passes_to": pt_to[tid][0], "runs_from": pt_from[tid][1], "runs_to": pt_to[tid][1]}
    gained = [t for t in shared if per_task[t]["pass_to"] > per_task[t]["pass_from"]]
    regressed = [t for t in shared if per_task[t]["pass_to"] < per_task[t]["pass_from"]]
    skipped = sorted((set(pt_from) | set(pt_to)) - set(shared))
    measurable = bool(imp.get("measurable")) and iqm_from["point"] is not None and iqm_to["point"] is not None
    if not n_from and not n_to:
        reason = f"neither {frm} nor {to} has a trace"
    elif not n_from:
        reason = f"{frm} has no trace"
    elif not n_to:
        reason = f"{to} has no trace"
    elif not shared:
        reason = f"no task has runs of both {frm} and {to}"
    elif not measurable:
        reason = imp.get("reason") or "the improvement cannot be measured"
    else:
        reason = None
    mean_from, mean_to = (agents.get(frm) or {}).get("mean_return"), (agents.get(to) or {}).get("mean_return")
    return {
        "measurable": measurable, "reason": reason,
        "improvement": _band(imp) if imp.get("measurable") else {"point": None, "lo": None, "hi": None},
        "iqm": {"from": iqm_from["point"], "to": iqm_to["point"],
                "delta": _r(iqm_to["point"] - iqm_from["point"]) if measurable else None,
                "from_lo": iqm_from["lo"], "from_hi": iqm_from["hi"], "to_lo": iqm_to["lo"], "to_hi": iqm_to["hi"]},
        "mean_return": {"from": mean_from, "to": mean_to,
                        "delta": _r(mean_to - mean_from) if mean_from is not None and mean_to is not None else None},
        "pass_rate": {"from": _r(pass_from), "to": _r(pass_to),
                      "delta": _r(pass_to - pass_from) if pass_from is not None and pass_to is not None else None,
                      "passes_from": p_from, "passes_to": p_to, "episodes_from": n_from, "episodes_to": n_to},
        "per_task": per_task, "gained": gained, "regressed": regressed,
        "tasks_skipped": skipped,
        "tasks_skipped_reason": ("present in one generation only, so nothing can be said about the step there"
                                 if skipped else None),
        "advisory": stats.get("advisory") or {"n_min": None, "tier": "none",
                                              "message": "no runs, so nothing to advise on"},
    }


def step_gaming(effect: dict, frm: str, to: str) -> dict:
    """Return up while the pass rate did not rise: the step bought reward,
    not correctness. ``return_delta`` is the mean return's, so a reader
    sees the raw sum the reward paid; the IQM sits beside it in ``effect``."""
    rd, pd = effect["mean_return"]["delta"], effect["pass_rate"]["delta"]
    if not effect["measurable"] or rd is None or pd is None:
        return {"measurable": False, "reason": effect["reason"] or "no pass rate on one side",
                "return_delta": rd, "pass_delta": pd, "flag": False, "reading": ""}
    pf, pt = effect["pass_rate"]["passes_from"], effect["pass_rate"]["passes_to"]
    nf, nt = effect["pass_rate"]["episodes_from"], effect["pass_rate"]["episodes_to"]
    flag = rd > 0 and (pt * nf <= pf * nt)   # pass_to <= pass_from, on the counts
    if flag:
        passes = (f"the pass rate fell {_num(-pd)} ({pf}/{nf} → {pt}/{nt})" if pd < 0
                  else f"the pass rate did not move ({pf}/{nf} → {pt}/{nt})")
        reading = (f"{to} earns {_signed(rd)} mean return over {frm} while {passes}: the step bought reward, "
                   f"not correctness")
    elif rd > 0:
        reading = f"{to} earns {_signed(rd)} mean return and passes more ({pf}/{nf} → {pt}/{nt}); not gamed"
    else:
        reading = f"{to}'s mean return did not rise ({_signed(rd)}); not gamed"
    return {"measurable": True, "reason": None, "return_delta": rd, "pass_delta": pd, "flag": flag,
            "reading": reading}


def step_overfit(effect: dict, triggers: dict, frm: str, to: str) -> dict:
    """The trigger tasks' mean return delta against the held-out tasks'."""
    base = {"measurable": False, "trigger_tasks": triggers["tasks"], "held_out_tasks": [],
            "unresolved": triggers["unresolved"], "trigger_delta": None, "held_out_delta": None,
            "gap": None, "margin": OVERFIT_MARGIN, "flag": False, "reading": ""}
    if not effect["measurable"]:
        return dict(base, reason=effect["reason"])
    shared = sorted(effect["per_task"])
    trig = [t for t in shared if t in triggers["tasks"]]
    held = [t for t in shared if t not in triggers["tasks"]]
    base["held_out_tasks"] = held
    if not triggers["tasks"]:
        return dict(base, reason=triggers["reason"] or "no trigger task")
    if not trig:
        return dict(base, reason=f"no trigger task has runs in both {frm} and {to}")
    if not held:
        return dict(base, reason="every shared task is a trigger task, so there is nothing held out")
    td = [effect["per_task"][t]["delta_return"] for t in trig if effect["per_task"][t]["delta_return"] is not None]
    hd = [effect["per_task"][t]["delta_return"] for t in held if effect["per_task"][t]["delta_return"] is not None]
    if not td or not hd:
        return dict(base, reason="a per-task return delta is missing")
    t_mean, h_mean = _mean(td), _mean(hd)
    gap = t_mean - h_mean
    flag = t_mean > 0 and gap >= OVERFIT_MARGIN and h_mean < OVERFIT_MARGIN
    if flag:
        reading = (f"{to} gains {_signed(t_mean)} return on the {_plural(len(trig), 'task')} that triggered the step "
                   f"({', '.join(trig)}) and {_signed(h_mean)} on the {len(held)} held out: a gap of {_num(gap)}, "
                   f"past the {_num(OVERFIT_MARGIN)} margin, so the step fits its evidence more than the rest")
    else:
        reading = (f"trigger tasks ({', '.join(trig)}) {_signed(t_mean)} return, held-out tasks {_signed(h_mean)}: "
                   f"gap {_num(gap)}" + (" under" if gap < OVERFIT_MARGIN else ", but the held-out tasks gained too, so not past")
                   + f" the {_num(OVERFIT_MARGIN)} margin")
    return dict(base, measurable=True, reason=None, trigger_delta=_r(t_mean), held_out_delta=_r(h_mean),
                gap=_r(gap), flag=flag, reading=reading)


def _top_branch(space: dict) -> Optional[dict]:
    br = (space or {}).get("branches") or {}
    pts = br.get("points") or []
    if not pts:
        return None
    p = pts[0]
    return {"id": p.get("id"), "depth": p.get("depth"), "prefix": list(p.get("prefix") or []),
            "imbalance": p.get("imbalance"), "score": p.get("score"), "label": p.get("label"),
            "sides": [{"policy": s.get("policy"), "token": s.get("token"), "mean_return": s.get("mean_return"),
                       "success_rate": s.get("success_rate")} for s in (p.get("sides") or [])]}


def step_drift(block: dict, frm: str, to: str) -> dict:
    space = block.get("space") or {}
    dist = space.get("distance") or {}
    within = dist.get("within") or {}
    if not space.get("measurable"):
        return {"measurable": False, "reason": space.get("reason") or "no behaviour to compare",
                "between": None, "spread_from": None, "spread_to": None, "top_branch": None}
    return {"measurable": dist.get("between") is not None,
            "reason": None if dist.get("between") is not None else "no cross pair to measure",
            "between": dist.get("between"),
            "spread_from": (within.get(frm) or {}).get("spread"), "spread_to": (within.get(to) or {}).get("spread"),
            "top_branch": _top_branch(space)}


def step_verdict(effect: dict, gaming: dict, overfit: dict) -> Optional[str]:
    """Exactly one verdict per step, in the module's stated precedence:
    gamed > forgot > overfit > traded > improved / regressed / flat;
    None when the effect is not measurable."""
    if not effect.get("measurable"):
        return None
    iqm_delta = effect["iqm"]["delta"]
    iqm_held = iqm_delta is not None and iqm_delta >= -1e-9
    if gaming.get("flag"):
        return "gamed"
    if effect["regressed"] and not effect["gained"] and iqm_held:
        return "forgot"
    if overfit.get("flag"):
        return "overfit"
    if effect["regressed"] and effect["gained"] and iqm_held:
        return "traded"
    lo, hi = effect["improvement"]["lo"], effect["improvement"]["hi"]
    if lo is not None and lo > 0.5:
        return "improved"
    if hi is not None and hi < 0.5:
        return "regressed"
    return "flat"


def _step_reading(step: dict) -> str:
    frm, to = step["from"], step["to"]
    eff, verdict = step["effect"], step["verdict"]
    change = step["diff"]["summary"]
    head = f"{frm} → {to} ({step['mechanism'] or 'unknown mechanism'}: {change})"
    if verdict is None:
        return f"{head}: the step's effect cannot be measured — {eff['reason']}."
    imp, iqm, pr = eff["improvement"], eff["iqm"], eff["pass_rate"]
    core = (f"P({to} > {frm}) {_pct(imp['point'])} [{_pct(imp['lo'])}, {_pct(imp['hi'])}], IQM "
            f"{_num(iqm['from'])} → {_num(iqm['to'])} ({_signed(iqm['delta'])}), passes "
            f"{pr['passes_from']}/{pr['episodes_from']} → {pr['passes_to']}/{pr['episodes_to']}")
    why = {
        "gamed": step["gaming"]["reading"],
        "forgot": (f"the pass rate fell on {', '.join(eff['regressed'])} while no task gained and the IQM held: "
                   f"the average hid the loss"),
        "overfit": step["overfit"]["reading"],
        "traded": f"{', '.join(eff['gained'])} gained passes and {', '.join(eff['regressed'])} lost them",
        "improved": "every resample keeps the improvement above the coin flip",
        "regressed": f"every resample keeps it below the coin flip, so {frm} is the one ahead",
        "flat": "the interval spans 50%, so these runs do not settle which generation is ahead",
    }[verdict]
    tail = []
    if step["diff"]["protected_touched"]:
        tail.append("touched protected " + ", ".join(step["diff"]["protected_touched"]))
    if step["diff"]["protected_restored"]:
        tail.append("restored protected " + ", ".join(step["diff"]["protected_restored"]))
    if verdict not in ("forgot", "traded") and eff["regressed"]:
        tail.append("passes fell on " + ", ".join(eff["regressed"]))
    n_min = (eff.get("advisory") or {}).get("n_min")
    if isinstance(n_min, int):
        tail.append(f"{_plural(n_min, 'run')} per task at the thinnest task, so every interval here is wide by "
                    f"construction")
    return f"{head} {verdict}: {core}; {why}" + ("; " + "; ".join(tail) if tail else "") + "."


# ---------------------------------------------------------------- per-generation

def _flags(row: dict) -> str:
    return "".join(letter for letter, on in (("e", row.get("error")), ("w", row.get("wasted")),
                                             ("d", row.get("decisive")), ("f", row.get("fault")),
                                             ("v", row.get("verifier"))) if on)


def _kind(step_type: Optional[str]) -> str:
    return "answer" if step_type == "answer" else "tool" if step_type in TOOLISH else "think"


def _read_generation_runs(gen: dict, gamma: float) -> list:
    """One reading per trace — the episode's rewards (recorded, else
    shaped, the same rule ``rl_aggregate`` applies), the timing ledger's
    wasted steps — done once and kept for the audit and the timeline."""
    from .reasoning import read_trace
    from .timing import time_attribution
    out = []
    for traj in gen["trajectories"]:
        reading = read_trace(traj)
        run = rl_run_from_trace(traj, gamma, reading=reading)
        timing = time_attribution(traj, reading)
        wasted = {r["index"]: r.get("wasted") for r in (timing.get("steps") or []) if isinstance(r, dict)}
        out.append({"traj": traj, "run": run, "wasted": wasted, "wasted_s": timing.get("wasted_s") or 0.0})
    return out


def _episode_entry(item: dict, pair_flags: dict, with_timeline: bool) -> dict:
    traj, run = item["traj"], item["run"]
    rewards = {r["step"]: r for r in run.get("rewards") or []}
    tools: dict = {}
    errors = 0
    timeline: list = []
    clock = 0.0
    marks = pair_flags.get((traj.agent.name, traj.task.id, traj.run_id))
    for pos, st in enumerate(traj.steps):
        lat = float(st.latency_s) if _number(st.latency_s) and st.latency_s >= 0 else 0.0
        if st.type in TOOLISH:
            tools[st.name or "?"] = tools.get(st.name or "?", 0) + 1
        if st.error:
            errors += 1
        row = rewards.get(st.index) or {}
        span_agent = (st.span or {}).get("agent") if isinstance(st.span, dict) else None
        flags = {"error": bool(st.error), "wasted": bool(item["wasted"].get(st.index)),
                 "verifier": isinstance(span_agent, str) and span_agent.lower().startswith("verif")}
        if marks is not None:
            m = marks.get(st.index) or {}
            flags["decisive"], flags["fault"] = bool(m.get("decisive")), bool(m.get("fault"))
        if with_timeline:
            timeline.append([round(clock, 4), round(lat, 4), _kind(st.type), st.name or st.type or "",
                             row.get("reward", 0.0), _flags(flags)])
        clock += lat
    entry = {"task_id": traj.task.id, "run_id": traj.run_id, "trace_id": traj.trace_id,
             "success": traj.outcome.success is True, "return": run.get("return"), "steps": len(traj.steps),
             "seconds": round(clock, 4), "tools": dict(sorted(tools.items())), "errors": errors,
             "wasted_s": _r(item["wasted_s"]), "source": run.get("source"),
             "flags_basis": "trace and pair report" if marks is not None else "trace only (d, f need the pair report)"}
    if with_timeline:
        entry["timeline"] = timeline
    return entry


def _pair_flags(reports: Optional[list]) -> dict:
    """``{(agent, task, run): {index: {decisive, fault}}}`` from the pair
    reports of the last step, via the impact layer's step facts — the
    flags only a pair can establish, for the episodes a pair covers."""
    if not reports:
        return {}
    from .impact import step_facts
    out: dict = {}
    for rep in reports:
        if not isinstance(rep, dict):
            continue
        tid = str((rep.get("task") or {}).get("id"))
        for side in ("a", "b"):
            block = rep.get(side) or {}
            name = str((block.get("agent") or {}).get("name"))
            steps = [s for s in (block.get("steps") or []) if isinstance(s, dict)]
            try:
                facts = step_facts(rep, side, steps)
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            out[(name, tid, str(block.get("run_id")))] = {f["index"]: {"decisive": f["decisive"], "fault": f["fault"]}
                                                          for f in facts}
    return out


def _audit_summary(runs: list, incoming_gamed: Optional[bool], gamma: float) -> dict:
    audit = audit_aggregate(runs, gamma=gamma)
    if not audit.get("measurable"):
        return {"measurable": False, "reason": audit.get("reason"), "return_up_pass_down": incoming_gamed,
                "inversions": None, "concentration": None, "critic": None, "narrative": audit.get("narrative")}
    reward = audit.get("reward") or {}
    dis = reward.get("disagreement") or {}
    by_task = (dis.get("scopes") or {}).get("by_task") or {}
    conc = reward.get("concentration") or {}
    critic = audit.get("critic") or {}
    overall = critic.get("overall") or {}
    return {
        "measurable": True, "reason": None, "return_up_pass_down": incoming_gamed,
        "inversions": ({"pairs_n": by_task.get("pairs_n"), "inversions": by_task.get("inversions"),
                        "basis": "within each task, (passed, failed) pairs the return orders the wrong way round"}
                       if dis.get("measurable") else {"pairs_n": None, "inversions": None, "basis": dis.get("reason")}),
        "concentration": ({"kind": conc.get("kind"), "mean_largest_share": conc.get("mean_largest_share"),
                           "mean_last_share": conc.get("mean_last_share")} if conc.get("measurable")
                          else {"kind": None, "reason": conc.get("reason")}),
        "critic": ({"measurable": True, "explained_variance": overall.get("explained_variance"),
                    "direction": overall.get("direction"), "mean_absolute_error": overall.get("mean_absolute_error"),
                    "n": overall.get("n")} if critic.get("measurable")
                   else {"measurable": False, "reason": critic.get("reason")}),
        "narrative": audit.get("narrative"),
    }


def _generation_stats(gen: dict, block: Optional[dict], name: Optional[str]) -> dict:
    agents = (block or {}).get("agents") or {}
    eps = (agents.get(name) or {}).get("episodes") or [] if name else []
    agg = ((block or {}).get("stats") or {}).get("aggregates") or {}
    iqm = _band((agg.get(name) or {}).get("iqm")) if name else _band(None)
    passes, n = _passes(eps)
    per_task = _per_task_passes(eps)
    return {"episodes_n": n, "tasks": sorted(per_task), "runs_per_task": {t: v[1] for t, v in sorted(per_task.items())},
            "passes": passes, "pass_rate": _r(passes / n) if n else None,
            "mean_return": (agents.get(name) or {}).get("mean_return") if name else None,
            "iqm": iqm, "source": (block or {}).get("source") if n else None}


# ---------------------------------------------------------------- the lineage

def _protected_state(artifacts, path: str):
    """The value at a protected path, for the taint that persists: a config
    value, presence in a list, a skill's body, the prompt text."""
    if not isinstance(artifacts, dict):
        return None
    kind, _, sub = path.partition(".")
    v = artifacts.get(kind)
    if not sub:
        return json.dumps(v, sort_keys=True)
    if isinstance(v, dict):
        return json.dumps(v.get(sub), sort_keys=True)
    if isinstance(v, list):
        named, _ = _named(v)
        if sub in named:
            return named[sub]
        return "present" if sub in {_item_key(x) for x in v} else "absent"
    return None


def _growth(generations: list, budget: dict) -> dict:
    keys = [k for k, _ in SIZE_OF]
    series = {k: [g["size"].get(k) for g in generations] for k in keys}
    over = []
    for g in generations:
        for what in sorted(budget):
            value = g["size"].get(what)
            if value is not None and value > budget[what]:
                over.append({"gen": g["id"], "what": what, "value": value, "budget": budget[what]})
    return {**series, "over_budget": over, "budget": dict(budget)}


def _from_origin(gens: list) -> list:
    """Mean normalised edit distance between every episode of the root and
    every episode of each later generation, over the same token streams
    the behaviour space uses."""
    streams = [[episode_tokens(t.steps)[:MAX_DISTANCE_TOKENS] for t in g["trajectories"]] for g in gens]
    out = []
    for i, g in enumerate(gens):
        if i == 0:
            out.append({"id": g["id"], "distance": None, "pairs": 0, "reason": "the origin"})
            continue
        if not streams[0] or not streams[i]:
            out.append({"id": g["id"], "distance": None, "pairs": 0,
                        "reason": "no episode on one side" if streams[0] else "the origin has no episode"})
            continue
        vals = [normalised_distance(a, b) for a in streams[0] for b in streams[i]]
        out.append({"id": g["id"], "distance": _r(_mean(vals)), "pairs": len(vals), "reason": None})
    return out


def _drift_reading(from_origin: list, consecutive: list) -> str:
    said = [f"{r['id']} {_num(r['distance'])}" for r in from_origin if r["distance"] is not None]
    parts = []
    if said:
        parts.append("distance from the origin's episodes: " + ", ".join(said))
    steps = [c for c in consecutive if c["distance"] is not None]
    if steps:
        top = max(steps, key=lambda c: (c["distance"], -c["index"]))
        parts.append(f"the largest single-step change in behaviour is {top['from']} → {top['to']} "
                     f"({_num(top['distance'])} apart)")
    return ("; ".join(parts) + " — normalised edit distance over the step-token streams, 0 = the same actions in "
            "the same order, 1 = nothing shared.") if parts else "no behaviour to measure drift over."


def _recommend(generations: list, steps: list, protected: list) -> tuple:
    """``(best, recommended)`` under the rule in the module docstring."""
    scored = [g for g in generations if g["iqm"]["point"] is not None]
    if not scored:
        none = {"id": None, "iqm": None, "why": "no generation has a measurable IQM"}
        return none, {"id": None, "is_last": False, "iqm": None, "why": none["why"]}
    best = max(scored, key=lambda g: (g["iqm"]["point"], -g["index"]))
    runner = max((g for g in scored if g is not best), key=lambda g: (g["iqm"]["point"], -g["index"]), default=None)
    best_why = (f"the highest IQM return, {_num(best['iqm']['point'])} [{_num(best['iqm']['lo'])}, "
                f"{_num(best['iqm']['hi'])}] over {_plural(best['episodes_n'], 'episode')}")
    if runner is not None:
        overlap = (runner["iqm"]["hi"] is not None and best["iqm"]["lo"] is not None
                   and runner["iqm"]["hi"] >= best["iqm"]["lo"])
        best_why += (f"; {runner['id']} is next at {_num(runner['iqm']['point'])}"
                     + (", with overlapping intervals, so these runs do not separate them" if overlap else ""))
    best_block = {"id": best["id"], "iqm": best["iqm"]["point"], "why": best_why}
    incoming = {s["to"]: s for s in steps}
    excluded: dict = {}
    taint: dict = {}
    for g in generations:
        step = incoming.get(g["id"])
        if step is not None:
            for c in step["diff"].get("protected_changes") or []:
                if c["direction"] == "restored":
                    taint.pop(c["path"], None)
                else:
                    taint[c["path"]] = step["to"]
        reasons = []
        if step is not None and step["verdict"] == "gamed":
            reasons.append("its incoming step was gamed")
        if taint:
            reasons.append("it runs with a weakened protected path: "
                           + ", ".join(f"{p} (since {since})" for p, since in sorted(taint.items())))
        if g["iqm"]["point"] is None:
            reasons.append("it has no measurable IQM")
        if reasons:
            excluded[g["id"]] = reasons
    eligible = [g for g in scored if g["id"] not in excluded]
    last = generations[-1]["id"]
    if not eligible:
        why = ("no generation is eligible: " + "; ".join(f"{gid} — {', '.join(r)}" for gid, r in excluded.items()))
        return best_block, {"id": None, "is_last": False, "iqm": None, "why": why}
    pick = max(eligible, key=lambda g: (g["iqm"]["point"], -g["index"]))
    if pick is best:
        why = f"{pick['id']} is the best generation and nothing disqualifies it"
    else:
        why = (f"{pick['id']} is the best eligible generation, IQM {_num(pick['iqm']['point'])} against "
               f"{best['id']}'s {_num(best['iqm']['point'])}; {best['id']} is passed over because "
               + ", ".join(excluded.get(best["id"], ["it is not eligible"])))
    later = [g for g in scored if g["index"] > pick["index"] and g["id"] not in excluded]
    if later:
        why += (f"; every later eligible generation ({', '.join(g['id'] for g in later)}) has a lower or equal IQM, "
                f"and a tie keeps the earlier one, which has fewer steps behind it")
    passed = [f"{gid} ({', '.join(r)})" for gid, r in excluded.items() if gid != best["id"] and gid != pick["id"]]
    if passed:
        why += "; also passed over: " + ", ".join(passed)
    if pick["id"] != last and last not in excluded:
        why += f"; the last generation, {last}, is eligible but its IQM is not higher"
    elif pick["id"] != last:
        why += f"; the last generation, {last}, is not eligible"
    return best_block, {"id": pick["id"], "is_last": pick["id"] == last, "iqm": pick["iqm"]["point"], "why": why}


def _integrity_reading(touched: list, restored: list, growth: dict) -> str:
    parts = []
    if touched:
        parts.append("protected paths changed: " + "; ".join(
            f"{t['path']} at {t['from_gen']} → {t['to_gen']} ({json.dumps(t['from'])} → {json.dumps(t['to'])}, {t['direction']})"
            for t in touched))
    else:
        parts.append("no step weakened a protected path")
    if restored:
        parts.append("restored: " + "; ".join(f"{t['path']} at {t['from_gen']} → {t['to_gen']}" for t in restored))
    over = growth.get("over_budget") or []
    if over:
        parts.append("over budget: " + "; ".join(f"{o['gen']} {o['what']} {o['value']} > {o['budget']}" for o in over))
    elif growth.get("budget"):
        parts.append("every generation is within budget")
    else:
        parts.append("no budget given, so growth is reported and not judged")
    return "; ".join(parts) + "."


def _advisory(generations: list) -> str:
    from .reliability import RUNS_FLOOR_STRUCTURED, runs_advisory
    counts = [n for g in generations for n in g["runs_per_task"].values()]
    base = runs_advisory(counts)
    msg = base.get("message") or "no runs, so nothing to advise on."
    n_min = base.get("n_min")
    tail = (" Every interval in this section is a stratified bootstrap over the runs recorded here, resampled "
            "within each task; it describes how much these runs' statistic moves when they are redrawn, not a "
            "population of runs never made.")
    if isinstance(n_min, int) and n_min < RUNS_FLOOR_STRUCTURED:
        tail += (f" At {_plural(n_min, 'run')} per task the intervals are wide by construction: read an overlap as "
                 f"'these runs do not separate the generations', never as 'they are equal', and read a single "
                 f"step's verdict as a description of these episodes.")
    return f"[{base.get('tier')}] {msg}{tail}"


def _narrative(ev: dict) -> str:
    gens, steps = ev["generations"], ev["steps"]
    if not gens:
        return ev.get("reason") or "No generation to read."
    tasks = sorted({t for g in gens for t in g["tasks"]})
    counts = sorted({n for g in gens for n in g["runs_per_task"].values()})
    runs = (f"{counts[0]} runs per task" if len(counts) == 1 else f"{counts[0]}–{counts[-1]} runs per task") if counts else "no runs"
    parts = [f"{ev['family'] or 'the lineage'}: {_plural(len(gens), 'generation')} ({gens[0]['id']} → {gens[-1]['id']}) "
             f"over {_plural(len(tasks), 'task')} at {runs}"]
    for s in steps:
        parts.append(s["reading"].rstrip("."))
    best, rec = ev["best"], ev["recommended"]
    if best["id"] is not None:
        parts.append(f"best: {best['id']} ({best['why']})")
    if rec["id"] is not None:
        parts.append(f"recommended: {rec['id']}" + ("" if rec["is_last"] else ", not the last generation") + f" — {rec['why']}")
    else:
        parts.append(f"recommended: none — {rec['why']}")
    parts.append(ev["integrity"]["reading"].rstrip("."))
    return "; ".join(parts) + "."


def evolve(lineage: dict, *, metric: str = "return", samples: int = BOOTSTRAP_SAMPLES, gamma: float = GAMMA,
           reports: Optional[list] = None) -> dict:
    """``aggregate["evolution"]`` for a lineage from :func:`read_lineage`.

    ``metric`` is any key of :data:`deepcompare.rlstats.METRICS`,
    ``samples`` the bootstrap resamples per statistic; ``reports`` (the
    last step's pair reports, when the caller has them) supply the
    decisive and fault flags of the timeline for the episodes they cover.
    Returns ``measurable: False`` with a reason when the lineage has no
    readable generation.
    """
    if metric not in METRICS:
        raise ValueError(f"unknown metric {metric!r}; choose one of {', '.join(sorted(METRICS))}")
    out: dict = {"version": VERSION, "measurable": bool(lineage.get("measurable")), "reason": lineage.get("reason"),
                 "family": lineage.get("family"), "protected": list(lineage.get("protected") or []),
                 "budget": dict(lineage.get("budget") or {}), "layout": lineage.get("layout"),
                 "order_basis": lineage.get("order_basis"), "metric": metric, "notes": list(lineage.get("notes") or []),
                 "generations": [], "steps": [], "trajectory": {}, "best": {}, "recommended": {},
                 "integrity": {}, "drift": {}, "narrative": "", "advisory": ""}
    gens = lineage.get("generations") or []
    if not out["measurable"] or not gens:
        out["measurable"] = False
        out["reason"] = out["reason"] or "no generation to read"
        out["narrative"] = out["reason"]
        return out
    protected = out["protected"]

    # one two-policy block per edge, both sides present; a generation's own
    # statistics are read off whichever edge carries it
    blocks: list = []
    for i in range(1, len(gens)):
        a, b = gens[i - 1], gens[i]
        if a["policy"] and b["policy"] and a["trajectories"] and b["trajectories"]:
            blocks.append(rl_aggregate([], a["trajectories"] + b["trajectories"], names=(a["policy"], b["policy"]),
                                       gamma=gamma, stats_metric=metric, stats_samples=samples))
        else:
            blocks.append(None)

    def block_for(i: int):
        for j in (i, i - 1):
            if 0 <= j < len(blocks) and blocks[j] is not None:
                return blocks[j]
        g = gens[i]
        if g["policy"] and g["trajectories"]:
            return rl_aggregate([], g["trajectories"], names=(g["policy"],), gamma=gamma,
                                stats_metric=metric, stats_samples=samples)
        return None

    # steps first: a generation's audit says whether its incoming step was gamed
    steps: list = []
    for i in range(1, len(gens)):
        a, b = gens[i - 1], gens[i]
        block = blocks[i - 1]
        a_art = (a["agent"] or {}).get("artifacts") if a["agent"] is not None else None
        b_art = (b["agent"] or {}).get("artifacts") if b["agent"] is not None else None
        diff = diff_artifacts(a_art, b_art, protected, from_id=a["id"], to_id=b["id"])
        if a["agent"] is None or b["agent"] is None:
            diff["reason"] = diff["reason"] or "agent.json unreadable on " + (
                a["id"] if a["agent"] is None else b["id"])
        evidence = (b["agent"] or {}).get("evidence") if b["agent"] is not None else None
        triggers = trigger_tasks(evidence, a)
        frm, to = a["policy"] or a["id"], b["policy"] or b["id"]
        if block is not None:
            effect = step_effect(block, frm, to)
        else:
            reason = (f"{a['id']} has no trace" if not a["trajectories"] else f"{b['id']} has no trace"
                      if not b["trajectories"] else "a generation has no policy name")
            effect = step_effect({"agents": {}, "stats": {}, "tasks": {}}, frm, to)
            effect["reason"] = reason
        gaming = step_gaming(effect, a["id"], b["id"])
        overfit = step_overfit(effect, triggers, a["id"], b["id"])
        drift = step_drift(block or {}, frm, to) if block is not None else \
            {"measurable": False, "reason": effect["reason"], "between": None, "spread_from": None,
             "spread_to": None, "top_branch": None}
        mech = (b["agent"] or {}).get("mechanism") if b["agent"] is not None else None
        step = {"from": a["id"], "to": b["id"], "index": i, "mechanism": mech if isinstance(mech, str) else None,
                "evidence": evidence if isinstance(evidence, dict) else None, "trigger_tasks": triggers["tasks"],
                "diff": diff, "effect": effect, "overfit": overfit, "gaming": gaming, "drift": drift,
                "verdict": step_verdict(effect, gaming, overfit), "findings": [], "reading": ""}
        if diff["protected_touched"]:
            step["findings"].append("protected")
        step["reading"] = _step_reading(step)
        steps.append(step)

    pair_flags = _pair_flags(reports)
    incoming = {s["to"]: s for s in steps}
    carried = 0
    out_gens: list = []
    for i, g in enumerate(gens):
        block = block_for(i)
        stats = _generation_stats(g, block, g["policy"])
        artifacts = (g["agent"] or {}).get("artifacts") if g["agent"] is not None else None
        readings = _read_generation_runs(g, gamma)
        step = incoming.get(g["id"])
        gamed = None if step is None else (step["gaming"]["flag"] if step["gaming"]["measurable"] else None)
        episodes = []
        capped = False
        for item in readings:
            with_timeline = carried < EPISODE_TIMELINE_CAP
            if not with_timeline:
                capped = True
            else:
                carried += 1
            episodes.append(_episode_entry(item, pair_flags, with_timeline))
        reason = g["error"] or (None if g["trajectories"] else
                                (g["notes"][-1] if g["notes"] and not g["trace_errors"] and g["policy"] is None
                                 else f"no trace read for {g['policy'] or g['id']}"))
        agent = g["agent"] or {}
        out_gens.append({
            "id": g["id"], "parent": g["parent"], "index": i, "dir": g["dir"],
            "measurable": g["agent"] is not None and bool(g["trajectories"]), "reason": reason,
            "mechanism": agent.get("mechanism") if isinstance(agent.get("mechanism"), str) else None,
            "evidence": agent.get("evidence") if isinstance(agent.get("evidence"), dict) else None,
            "policy": g["policy"], **stats,
            "size": artifact_size(artifacts), "artifacts_digest": artifact_digest(artifacts),
            "audit": _audit_summary([r["run"] for r in readings], gamed, gamma),
            "note": agent.get("note") if isinstance(agent.get("note"), str) else None,
            "notes": list(g["notes"]) + list(g["trace_errors"]),
            "episodes": episodes, "episodes_capped": capped,
        })
        if step is not None:
            over = [o for o in _growth([out_gens[-1]], out["budget"])["over_budget"]]
            if over:
                step["findings"].append("over_budget")
    out["generations"] = out_gens
    out["steps"] = steps

    counts = {v: sum(1 for s in steps if s["verdict"] == v) for v in VERDICTS}
    measurable_steps = [s for s in steps if s["verdict"] is not None]
    scored = [g for g in out_gens if g["iqm"]["point"] is not None]
    out["trajectory"] = {
        **counts, "unmeasurable": len(steps) - len(measurable_steps), "steps": len(steps),
        "net_iqm_delta": _r(scored[-1]["iqm"]["point"] - scored[0]["iqm"]["point"]) if len(scored) >= 2 else None,
        "monotone": (all(s["effect"]["iqm"]["delta"] >= -1e-9 for s in measurable_steps) if measurable_steps else None),
        "cumulative": [{"id": g["id"], "iqm": g["iqm"]["point"], "lo": g["iqm"]["lo"], "hi": g["iqm"]["hi"],
                        "pass_rate": g["pass_rate"], "mean_return": g["mean_return"]} for g in out_gens],
    }
    growth = _growth(out_gens, out["budget"])
    touched = [{"step": s["index"], "from_gen": s["from"], "to_gen": s["to"], "path": c["path"], "from": c["from"],
                "to": c["to"], "direction": c["direction"]}
               for s in steps for c in (s["diff"].get("protected_changes") or []) if c["direction"] != "restored"]
    restored = [{"step": s["index"], "from_gen": s["from"], "to_gen": s["to"], "path": c["path"], "from": c["from"],
                 "to": c["to"], "direction": c["direction"]}
                for s in steps for c in (s["diff"].get("protected_changes") or []) if c["direction"] == "restored"]
    unreadable = sorted({s["to"] for s in steps if not s["diff"]["measurable"]})
    out["integrity"] = {"touched": touched, "restored": restored, "growth": growth,
                        "unreadable": unreadable, "reading": _integrity_reading(touched, restored, growth)
                        + (f" The artifacts of {', '.join(unreadable)} could not be read, so nothing is said about "
                           f"them." if unreadable else "")}
    consecutive = [{"from": s["from"], "to": s["to"], "index": s["index"], "distance": s["drift"]["between"]}
                   for s in steps]
    from_origin = _from_origin(gens)
    out["drift"] = {"from_origin": from_origin, "consecutive": consecutive,
                    "reading": _drift_reading(from_origin, consecutive)}
    out["best"], out["recommended"] = _recommend(out_gens, steps, protected)
    out["advisory"] = _advisory(out_gens)
    out["narrative"] = _narrative(out)
    return out


def analyse_lineage(path, *, layout: str = "native", metric: str = "return", samples: int = BOOTSTRAP_SAMPLES,
                    gamma: float = GAMMA, reports: Optional[list] = None) -> dict:
    """:func:`read_lineage` then :func:`evolve`, for callers that only want
    the section."""
    return evolve(read_lineage(path, layout), metric=metric, samples=samples, gamma=gamma, reports=reports)


def last_pair(lineage: dict) -> Optional[tuple]:
    """The last two generations that both have traces — the pair the
    ``evolve`` command writes an ordinary runs output for — or None."""
    gens = [g for g in (lineage.get("generations") or []) if g["trajectories"]]
    if len(gens) < 2:
        return None
    return gens[-2], gens[-1]


def fail_on(evolution: dict, names) -> list:
    """The steps that carry any of ``names`` (a verdict or a finding), as
    ``(step_index, name)`` — the CLI's ``--fail-on`` answer."""
    wanted = {n.strip() for n in names if n and n.strip()}
    unknown = sorted(wanted - set(VERDICTS) - set(FINDINGS))
    if unknown:
        raise ValueError(f"unknown --fail-on name(s): {', '.join(unknown)}; choose from "
                         f"{', '.join(VERDICTS + FINDINGS)}")
    hits = []
    for s in evolution.get("steps") or []:
        for name in sorted(wanted):
            if s.get("verdict") == name or name in (s.get("findings") or []):
                hits.append((s["index"], name))
    return hits


__all__ = ["read_lineage", "evolve", "analyse_lineage", "diff_artifacts", "artifact_size", "artifact_digest",
           "trigger_tasks", "step_effect", "step_gaming", "step_overfit", "step_drift", "step_verdict",
           "last_pair", "fail_on", "VERSION", "VERDICTS", "FINDINGS", "OVERFIT_MARGIN", "EPISODE_TIMELINE_CAP",
           "ARTIFACT_KINDS", "LAYOUTS"]
